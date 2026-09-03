"""Démo Text-to-SQL — interface Streamlit.

Un onglet par tool SQL de la conception (``ask_database``, ``get_schema``,
``check_stock``, ``order_status``), même patron que ``app.py`` pour le RAG. Le point
central à démontrer : le **profil** (sidebar) change le comportement de tous les
onglets — colonnes sensibles absentes du schéma, requêtes refusées — sans qu'aucun
tool ne prenne jamais le profil en paramètre (il est injecté à la construction du
moteur, jamais falsifiable par un appelant).

Usage : ``make ui-sql`` ou ``uv run streamlit run app_sql.py``. Nécessite
``make seed`` au préalable (``data/sorabel.db``). Note pour le développement : comme
pour ``app.py``, Streamlit ne recharge jamais les modules déjà importés (``sql/``,
``gateway/``) — après une modification, tuer et relancer le process plutôt que
compter sur le rechargement à chaud.
"""

from pathlib import Path

import streamlit as st
from langfuse import Langfuse, get_client
from langfuse.openai import OpenAI

from gateway.settings import get_settings
from sql.access import PROFILES, StaticAccessRules
from sql.engine import SqlEngine
from sql.trace import JsonlTraceRecorder

MAIN_JOURNAL = Path("logs/mcp_audit.jsonl")  # journal unique, cf. conception § « Journal MCP unique »


@st.cache_resource
def load_engine(profile: str) -> SqlEngine:
    """Construit (et met en cache par profil) le moteur — deux instances vivent en
    cache, une par valeur du sélecteur sidebar, jamais reconstruites à chaque clic.
    """
    settings = get_settings()
    trace = JsonlTraceRecorder(MAIN_JOURNAL, settings.sql_alert_log)
    return SqlEngine(
        profile=profile,
        access_rules=StaticAccessRules(),
        trace=trace,
        llm_client=load_llm(),
        settings=settings,
    )


@st.cache_resource
def load_llm() -> OpenAI:
    """Client OpenAI (Azure), tracé sur Langfuse Cloud — mis en cache une seule fois.

    Le singleton Langfuse est enregistré ici, avec les credentials de ``Settings``
    (pas une lecture implicite de ``os.environ``), avant toute construction de client
    OpenAI dans ce process — cohérent avec ``scripts/eval_sql.py``.
    """
    settings = get_settings()
    Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_base_url,
    )
    return OpenAI(base_url=settings.azure_ai_endpoint, api_key=settings.azure_ai_api_key)


STATUS_RENDER = {
    "ok": st.success,
    "refused": st.error,
    "clarification": st.warning,
}

st.set_page_config(page_title="Sorabel Text-to-SQL — démo", page_icon="🗄️")
st.title("🗄️ Sorabel — démo Text-to-SQL")
st.caption(
    "Génération SQL validée + tools figés, lecture seule garantie par cumul de "
    "barrières indépendantes (authorizer SQLite, validation, EXPLAIN QUERY PLAN)."
)

with st.sidebar:
    st.header("Profil")
    profile = st.selectbox(
        "Profil de connexion", sorted(PROFILES),
        help="Résolu côté serveur dans le vrai système — ici, un sélecteur de démo "
             "joue ce rôle. Change le résultat de TOUS les onglets, sans qu'aucun "
             "tool ne le reçoive en paramètre.",
    )
    show_sql = st.toggle(
        "Afficher le SQL généré / exécuté", value=True,
        help="Le moteur les expose toujours (la trace en a besoin) — les montrer "
             "au client est une décision de la couche MCP, pas de ce moteur "
             "(spec § 4.9). Ici, en démo, on choisit de les montrer.",
    )

tab_ask, tab_schema, tab_stock, tab_order = st.tabs([
    "🔎 Poser une question (ask_database)",
    "📋 Schéma (get_schema)",
    "📦 Stock (check_stock)",
    "🧾 Commande (order_status)",
])

with tab_ask:
    st.caption(
        "Classification + génération en un seul appel structuré : `SQL_GENERABLE`, "
        "`AMBIGUOUS` ou `OUT_OF_SCHEMA`. Les tentatives d'écriture sont détectées "
        "avant même l'appel au modèle (spec § 4.11)."
    )
    question = st.text_input(
        "Question", placeholder="ex. quel est le stock total de la REF-8842 ?"
    )
    if question:
        engine = load_engine(profile)
        with st.spinner("Génération et validation…"):
            resultat = engine.ask_database(question)

        STATUS_RENDER[resultat.status](
            f"**{resultat.status}**" + (f"  ·  code=`{resultat.code}`" if resultat.code else "")
        )
        if resultat.message:
            st.write(resultat.message)

        if show_sql and (resultat.sql_genere or resultat.sql_execute):
            st.code(resultat.sql_execute or resultat.sql_genere, language="sql")
            if resultat.sql_execute and resultat.sql_execute != resultat.sql_genere:
                st.caption("SQL généré (avant ajout du LIMIT) :")
                st.code(resultat.sql_genere, language="sql")

        if resultat.status == "ok":
            st.caption(
                f"{resultat.row_count} ligne(s)"
                + (" — **tronqué**" if resultat.truncated else "")
            )
            if resultat.rows:
                st.dataframe(
                    [dict(zip(resultat.columns, row, strict=True)) for row in resultat.rows],
                    use_container_width=True,
                )

        # Flush explicite : Streamlit reste un process long, mais on veut voir la
        # trace apparaître sur le dashboard tout de suite pendant une démo live.
        get_client().flush()

with tab_schema:
    st.caption(
        "Équivalent du tool `get_schema` : structure lue à la source (PRAGMA), "
        "filtrée selon le profil sélectionné en sidebar."
    )
    engine = load_engine(profile)
    schema = engine.get_schema()
    for table in schema.tables:
        with st.expander(f"**{table.name}** — {table.description}", expanded=False):
            st.dataframe(
                [
                    {
                        "colonne": c.name,
                        "type": c.type,
                        "description": c.description,
                        "valeurs": ", ".join(c.values) if c.values else "",
                    }
                    for c in table.columns
                ],
                use_container_width=True,
            )
    st.markdown("**Relations**")
    for relation in schema.relations:
        st.caption(relation)

with tab_stock:
    st.caption("Équivalent du tool `check_stock` : SQL figé, sans LLM.")
    ref = st.text_input("Référence produit", value="REF-8842")
    if ref:
        engine = load_engine(profile)
        stock = engine.check_stock(ref)
        if not stock.found:
            st.warning(f"Référence introuvable : {ref}")
        else:
            st.metric("Stock total", stock.total_quantity)
            st.dataframe(
                [{"entrepôt": w.entrepot, "quantité": w.quantite} for w in stock.by_warehouse],
                use_container_width=True,
            )

with tab_order:
    st.caption("Équivalent du tool `order_status` : SQL figé, sans LLM.")
    order_id = st.text_input("Identifiant de commande", value="CMD-2026-0042")
    if order_id:
        engine = load_engine(profile)
        commande = engine.order_status(order_id)
        if not commande.found:
            st.warning(f"Commande introuvable : {order_id}")
        else:
            cols = st.columns(3)
            cols[0].metric("Statut", commande.status)
            cols[1].metric("Date", commande.date_commande)
            cols[2].metric("Montant HT", f"{commande.montant_ht:.2f} €")
