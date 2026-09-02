"""Démo RAG hybride + rerank — interface Streamlit.

Reprend scripts/demo_agent.py avec une interface visuelle, un onglet par tool RAG de
la conception (answer_question, search_docs, get_document, list_sources) plutôt qu'une
seule boucle de questions. Pas un livrable d'interface graphique complet (SQL, matrice
d'accès) : juste le RAG, pour la démo.

Usage : ``make ui`` ou ``uv run streamlit run app.py``. Note pour le développement :
Streamlit relance ce script à chaque interaction mais ne recharge jamais les modules
déjà importés (``retrieval/``, ``gateway/``) — après une modification de ces modules,
il faut tuer et relancer le process, pas compter sur le rechargement à chaud.
"""

import streamlit as st
from openai import OpenAI

from gateway.chroma import chroma_client, open_collection
from gateway.embedder import AzureEmbedder
from gateway.settings import get_settings
from retrieval.answer import compose_answer, format_citation
from retrieval.engine import SearchEngine
from retrieval.reranker import AzureCohereReranker

CORPUS_DIR = get_settings().corpus_dir
SOURCE_MIME = {"pdf": "application/pdf", "html": "text/html", "md": "text/markdown"}

# Vérifiés présents dans le corpus (un par collection) — pas de saisie libre pour la démo.
EXAMPLE_DOCUMENT_IDS = [
    "REF-1024-v2.1",
    "REF-8842-v2.1",
    "notice-REF-1459-v1.1",
    "proc-casse-transport-01-v2.0",
    "note-2024-01-11-alerte-qualite-50",
]

STAGE_LABELS = {
    "reference": "0. Routing par référence exacte — `retrieval/routing.py`",
    "dense": "1. Dense (distance L2, plus bas = plus proche) — `retrieval/dense.py`",
    "lexical": "2. BM25 (score, plus haut = meilleur) — `retrieval/lexical.py`",
    "fused": "3. Fusion RRF (score, plus haut = meilleur) — `retrieval/fusion.py`",
    "versioned": "4. Dernière version par famille (anti-doublons) — `retrieval/dedup.py`",
    "diversified": "5. Diversification par thème (anti-quasi-doublons) — `retrieval/dedup.py`",
    "reranked": "6. Rerank Cohere — `retrieval/reranker.py`",
}


@st.cache_resource
def load_engine(rerank_enabled: bool) -> SearchEngine:
    """Construit (et met en cache par valeur de ``rerank_enabled``) le moteur de recherche.

    ``@st.cache_resource`` évite de reconstruire l'index BM25 à chaque interaction —
    Streamlit relance ce script du haut en bas à chaque clic, mais le cache lui fait
    sauter cet appel s'il a déjà tourné avec le même argument. Deux instances vivent
    en cache (une par valeur du toggle sidebar), pas une seule.
    """
    settings = get_settings().model_copy(update={"rerank_enabled": rerank_enabled})
    collection = open_collection(chroma_client(settings), settings.chroma_collection)
    reranker = AzureCohereReranker(settings) if rerank_enabled else None
    return SearchEngine(collection, AzureEmbedder(settings), settings, reranker=reranker)


@st.cache_resource
def load_llm() -> OpenAI:
    """Client OpenAI (Azure) pour la rédaction de réponse, mis en cache une seule fois."""
    settings = get_settings()
    return OpenAI(base_url=settings.azure_ai_endpoint, api_key=settings.azure_ai_api_key)


st.set_page_config(page_title="Sorabel RAG — démo", page_icon="🔎")
st.title("🔎 Sorabel — démo RAG hybride + rerank")
st.caption(
    "Dense + BM25 + RRF + rerank Cohere, refus hors-corpus calibré (E1). "
    "Démo du chantier RAG uniquement — pas de matrice d'accès, pas de SQL."
)

with st.sidebar:
    st.header("Options")
    rerank_on = st.toggle("Rerank activé", value=True, help="Sans rerank : pas de refus fiable (spec §4.3)")
    show_stages = st.toggle("Détailler le pipeline", value=False)
    show_answer = st.toggle("Générer une réponse (gpt-5.4-mini)", value=True)

tab_search, tab_raw, tab_document, tab_sources = st.tabs([
    "🔎 Recherche (answer_question)",
    "🧪 Recherche brute (search_docs)",
    "📄 Récupérer un document (get_document)",
    "🗂️ Lister les sources (list_sources)",
])

with tab_search:
    question = st.text_input(
        "Question", placeholder="ex. que faire si un colis arrive endommagé ?"
    )

    if question:
        engine = load_engine(rerank_on)
        outcome = engine.search(question)

        st.markdown(f"**Route :** `{outcome.route}`")

        if show_stages:
            with st.expander("Étapes du pipeline (retrieval/engine.py)", expanded=True):
                for key, label in STAGE_LABELS.items():
                    if key in outcome.stages:
                        ids = outcome.stages[key]
                        scores = outcome.stage_scores.get(key)
                        st.write(f"**{label}** — {len(ids)} candidats")
                        if scores:
                            lines = [f"{cid}  ({scores[cid]:.4f})" for cid in ids[:5]]
                        else:
                            lines = ids[:5]
                        st.code("\n".join(lines) + ("\n…" if len(ids) > 5 else ""))

        if outcome.is_refusal:
            st.error(f"❌ Refus — {outcome.reason}")
        elif not outcome.hits:
            st.warning(f"Aucun résultat — {outcome.reason}")
        else:
            st.subheader("Passages retenus")
            for position, hit in enumerate(outcome.hits, start=1):
                score = f"{hit.rerank_score:.4f}" if hit.rerank_score is not None else "—"
                st.markdown(f"**[{position}]** score=`{score}`  {format_citation(hit.chunk)}")
                with st.expander("texte du passage"):
                    st.write(hit.chunk.content)

            if show_answer:
                settings = get_settings()
                with st.spinner("Génération de la réponse…"):
                    answer = compose_answer(
                        load_llm(), settings.azure_model_text_generation, question, outcome.hits
                    )
                st.subheader("Réponse")
                st.write(answer)

with tab_raw:
    st.caption(
        "Équivalent du tool `search_docs` : Dense + BM25 + RRF, "
        "**sans** dédup de version, sans diversification, sans rerank ni refus."
    )
    raw_query = st.text_input("Requête", key="raw_query", placeholder="ex. colis endommagé")
    include_score = st.checkbox("Afficher le score RRF (include_score)", value=True)
    if raw_query:
        engine = load_engine(rerank_on)
        response = engine.search_docs(raw_query, top_k=10, include_score=include_score)
        st.caption(f"{response.retrieval_count} candidats fusionnés, top {len(response.results)} affichés")
        for r in response.results:
            score = f"{r.rrf_score:.5f}" if r.rrf_score is not None else "—"
            st.markdown(f"**[{r.rank}]** score=`{score}`  {r.title}" + (f" — {r.ref_produit}" if r.ref_produit else ""))
            document_id = r.chunk_id.rsplit("#", 1)[0]
            st.download_button(
                "⬇️ Télécharger le texte extrait",
                data=r.content,
                file_name=f"{document_id}.txt",
                mime="text/plain",
                key=f"dl_raw_{r.chunk_id}",
            )

with tab_document:
    st.caption("Équivalent du tool `get_document` : lookup direct par identifiant, sans recherche.")
    document_id = st.selectbox("Document", EXAMPLE_DOCUMENT_IDS)
    if document_id:
        engine = load_engine(rerank_on)
        doc = engine.get_document(document_id)
        if doc is None:
            st.warning(f"Document introuvable : {document_id}")
        else:
            st.markdown(f"**{format_citation(doc)}**")
            cols = st.columns(3)
            cols[0].metric("Collection", doc.collection)
            cols[1].metric("Type", doc.type_doc)
            cols[2].metric("Version", doc.version)
            st.text_area("Contenu (texte extrait)", doc.content, height=250)

            dl1, dl2 = st.columns(2)
            dl1.download_button(
                "⬇️ Télécharger le texte extrait",
                data=doc.content,
                file_name=f"{document_id}.txt",
                mime="text/plain",
            )
            source_path = CORPUS_DIR / doc.collection / f"{document_id}.{doc.source}"
            if source_path.is_file():
                dl2.download_button(
                    "⬇️ Télécharger le fichier original",
                    data=source_path.read_bytes(),
                    file_name=source_path.name,
                    mime=SOURCE_MIME.get(doc.source, "application/octet-stream"),
                )
            else:
                dl2.caption("Fichier original introuvable sur le disque.")

with tab_sources:
    st.caption(
        "Équivalent du tool `list_sources` : énumération par métadonnées, regroupée par "
        "famille (une entrée par document logique, versions antérieures optionnelles)."
    )
    col1, col2 = st.columns(2)
    collection_filter = col1.selectbox(
        "Collection", [None, "fiches", "notices", "sav", "notes"], format_func=lambda c: c or "Toutes"
    )
    ref_filter = col2.text_input("Référence produit (ex. REF-8842)", value="")
    include_versions = st.checkbox("Inclure les versions antérieures", value=False)

    engine = load_engine(rerank_on)
    sources_response = engine.list_sources(
        collection=collection_filter,
        ref_produit=ref_filter or None,
        include_versions=include_versions,
    )
    st.caption(
        f"{sources_response.total_count} famille(s) — filtres : "
        f"{sources_response.filters_applied or 'aucun'}"
    )
    for source in sources_response.sources:
        cv = source.current_version
        label = f"**{cv.title}**" + (f" — {source.ref_produit}" if source.ref_produit else "")
        label += f"  ·  {source.collection} / {source.type_doc}  ·  v{cv.version} ({cv.date})"
        st.markdown(label)
        if include_versions and source.older_versions:
            for v in source.older_versions:
                st.caption(f"↳ version antérieure : v{v.version} ({v.date}) — {v.document_id}")
