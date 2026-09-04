"""Orchestration du retrieval hybride — module central du chantier RAG.

``SearchEngine`` est le point d'entrée unique de tout le pipeline : c'est la classe
que ``app.py`` et ``scripts/demo_agent.py`` instancient, et celle que le futur serveur
MCP (chantier suivant) appellera à son tour. Elle expose quatre méthodes, chacune
équivalente à un tool RAG de la conception : ``search`` (answer_question, avec
dédup/diversification/rerank/refus), ``search_docs`` (mode brut, exploration),
``get_document`` (lookup direct) et ``list_sources`` (énumération par métadonnées).

Le cœur du pipeline hybride est ``_search_hybrid`` : dense (Chroma) + BM25 (lexical.py)
→ fusion RRF (fusion.py) → dédup de version + diversification thématique (dedup.py)
→ rerank Cohere (reranker.py) → décision de refus si le meilleur score reranké est
sous le seuil calibré (spec § 4.3, § 5). Chaque étape produit une liste de chunk_id
ordonnée, conservée dans ``stages`` (et son score dans ``stage_scores``) pour
l'affichage pédagogique de la démo (--show-stages, sidebar Streamlit).

``SearchEngine`` ne garde jamais le corpus complet en mémoire au-delà de sa
construction : chaque méthode ne rapatrie depuis Chroma (retrieval/corpus.py) que les
chunk_id dont elle a besoin à cet instant — un identifiant précis, les candidats d'un
étage, ou tout le corpus uniquement pour ``list_sources`` (une énumération, par
nature). Seul ``LexicalIndex`` retient une trace du corpus entier, sous la forme
compacte de son index BM25.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from chromadb.api.models.Collection import Collection

from gateway.embedder import Embedder
from gateway.settings import Settings
from retrieval.corpus import IndexedChunk, by_chunk_id, load_chunks
from retrieval.dedup import diversify, keep_latest_version, version_key
from retrieval.dense import dense_search, dense_search_with_distances
from retrieval.fusion import reciprocal_rank_fusion, rrf_scores
from retrieval.lexical import LexicalIndex
from retrieval.reranker import Reranker
from retrieval.routing import detect_reference, lookup_by_reference


@dataclass(frozen=True)
class Hit:
    """Un résultat retenu par ``search()`` : le chunk complet et son score de rerank.

    Unité de base consommée par retrieval/answer.py (compose_answer) et par l'affichage
    (app.py, scripts/demo_agent.py) — jamais un chunk_id brut une fois sorti du moteur.
    """

    chunk: IndexedChunk
    rerank_score: float | None  # None si le rerank est désactivé ou hors chemin


@dataclass(frozen=True)
class SearchOutcome:
    """Résultat complet d'un ``search()`` : les hits (ou un refus) plus toute la
    trace du pipeline, utile pour la démo et le débogage mais pas pour un usage
    en production où seuls ``hits``/``is_refusal``/``reason`` comptent réellement.
    """

    hits: list[Hit]
    is_refusal: bool
    reason: str | None = None
    route: str = "hybrid"  # "reference" | "hybrid"
    stages: dict[str, list[str]] = field(default_factory=dict)
    # Score par étage, pour l'affichage pédagogique — clés parmi "dense" (distance L2,
    # plus bas = plus proche), "lexical" (BM25, plus haut = meilleur), "fused" (RRF,
    # plus haut = meilleur). Absent pour "versioned"/"diversified" (filtres, pas de
    # score propre) et "reranked" (déjà porté par Hit.rerank_score).
    stage_scores: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchDocResult:
    chunk_id: str
    document_id: str
    rank: int  # position dans le top-k, 1-indexed
    title: str
    type_doc: str
    ref_produit: str | None
    version: str
    date: str
    source: str
    content: str
    rrf_score: float | None  # présent seulement si include_score=True


@dataclass(frozen=True)
class SearchDocsResponse:
    results: list[SearchDocResult]
    query: str
    retrieval_count: int  # nombre de candidats testés avant le top_k


@dataclass(frozen=True)
class VersionSummary:
    document_id: str
    version: str
    date: str


@dataclass(frozen=True)
class CurrentVersionSummary:
    document_id: str
    title: str
    version: str
    date: str
    chunk_count: int  # toujours 1 sur ce corpus (1 chunk = 1 document)


@dataclass(frozen=True)
class SourceSummary:
    family_id: str
    current_version: CurrentVersionSummary
    older_versions: list[VersionSummary]  # vide si include_versions=False
    ref_produit: str | None
    type_doc: str
    collection: str


@dataclass(frozen=True)
class ListSourcesResponse:
    sources: list[SourceSummary]
    total_count: int  # nombre de familles, pas de chunks
    filters_applied: dict[str, str]


class SearchEngine:
    def __init__(
        self,
        collection: Collection,
        embedder: Embedder,
        settings: Settings,
        reranker: Reranker | None = None,
    ) -> None:
        self._collection = collection
        self._embedder = embedder
        self._settings = settings
        self._reranker = reranker
        # BM25 doit voir tout le corpus une fois pour calculer ses statistiques
        # (IDF, spec § 4.6) — mais contrairement à avant, ce résultat n'est pas
        # conservé sur self : seul l'index BM25 construit à partir de lui persiste.
        # Toute autre méthode (get_document, list_sources, _search_hybrid...)
        # refait un appel Chroma ciblé plutôt que de lire un cache local du corpus.
        self._lexical = LexicalIndex(load_chunks(collection))

    def get_document(self, document_id: str) -> IndexedChunk | None:
        """Équivalent du tool get_document : lookup direct, sans recherche.

        Un seul appel Chroma ciblé sur l'identifiant demandé (``ids=[...]``), pas de
        cache local du corpus à consulter.
        """
        found = load_chunks(self._collection, ids=[f"{document_id}#0"])
        return found[0] if found else None

    def list_sources(
        self,
        collection: str | None = None,
        type_doc: str | None = None,
        ref_produit: str | None = None,
        include_versions: bool = False,
    ) -> ListSourcesResponse:
        """Équivalent du tool list_sources : énumération par métadonnées, sans recherche.

        Regroupe par family_id : une entrée par document logique, la version la plus
        récente en current_version — pas la version qui serait la mieux classée dans
        un retrieval, il n'y a pas de retrieval ici. Seule méthode qui rapatrie tout
        le corpus (c'est la nature même d'une énumération), mais seulement à l'appel
        — jamais préchargé au démarrage de ``SearchEngine``.
        """
        chunks = load_chunks(self._collection)
        filters_applied: dict[str, str] = {}
        if collection is not None:
            chunks = [c for c in chunks if c.collection == collection]
            filters_applied["collection"] = collection
        if type_doc is not None:
            chunks = [c for c in chunks if c.type_doc == type_doc]
            filters_applied["type_doc"] = type_doc
        if ref_produit is not None:
            chunks = [c for c in chunks if c.ref_produit == ref_produit]
            filters_applied["ref_produit"] = ref_produit

        by_family: dict[str, list[IndexedChunk]] = defaultdict(list)
        for chunk in chunks:
            by_family[chunk.family_id].append(chunk)

        sources = []
        for family_id in sorted(by_family):
            ordered = sorted(
                by_family[family_id], key=lambda c: version_key(c.version), reverse=True
            )
            current, older = ordered[0], ordered[1:]
            sources.append(
                SourceSummary(
                    family_id=family_id,
                    current_version=CurrentVersionSummary(
                        document_id=current.document_id,
                        title=current.title,
                        version=current.version,
                        date=current.date,
                        chunk_count=1,
                    ),
                    older_versions=[
                        VersionSummary(document_id=c.document_id, version=c.version, date=c.date)
                        for c in older
                    ] if include_versions else [],
                    ref_produit=current.ref_produit,
                    type_doc=current.type_doc,
                    collection=current.collection,
                )
            )
        return ListSourcesResponse(
            sources=sources, total_count=len(sources), filters_applied=filters_applied
        )

    def search_docs(
        self, query: str, top_k: int = 5, include_score: bool = False
    ) -> SearchDocsResponse:
        """Équivalent du tool search_docs : recherche hybride brute.

        Dense + BM25 + RRF, sans dédup de version, sans diversification, sans rerank
        ni décision de refus — exploration/debug, pas answer_question (conception
        tools_rag_mcp.md § 2). Les métadonnées ne sont rapatriées de Chroma que pour
        les ``top_k`` résultats finalement retournés, pas pour tous les candidats
        fusionnés.

        Le routing déterministe par référence (retrieval/routing.py) s'applique ici
        aussi, pas seulement à ``search()`` : un classement dense/BM25 peut confondre
        deux références lexicalement proches (``REF-8842`` / ``REF-8443``, découvert
        via l'acceptance suite du chantier 3), alors que E2 exige une précision totale
        sur une référence exacte, quel que soit le tool appelé.
        """
        reference = detect_reference(query)
        if reference is not None:
            return self._search_docs_by_reference(reference, top_k)

        cfg = self._settings
        dense = dense_search(self._collection, self._embedder, query, cfg.dense_candidates)
        lexical = self._lexical.search(query, cfg.lexical_candidates)
        scores = rrf_scores([dense, lexical], k=cfg.rrf_k)
        fused = sorted(scores, key=lambda cid: scores[cid], reverse=True)
        top_ids = fused[:top_k]
        by_id = by_chunk_id(load_chunks(self._collection, ids=top_ids))
        results = [
            SearchDocResult(
                chunk_id=chunk_id,
                document_id=by_id[chunk_id].document_id,
                rank=rank,
                title=by_id[chunk_id].title,
                type_doc=by_id[chunk_id].type_doc,
                ref_produit=by_id[chunk_id].ref_produit,
                version=by_id[chunk_id].version,
                date=by_id[chunk_id].date,
                source=by_id[chunk_id].source,
                content=by_id[chunk_id].content,
                rrf_score=scores[chunk_id] if include_score else None,
            )
            for rank, chunk_id in enumerate(top_ids, start=1)
        ]
        return SearchDocsResponse(results=results, query=query, retrieval_count=len(fused))

    def _search_docs_by_reference(self, reference: str, top_k: int) -> SearchDocsResponse:
        """Lookup déterministe pour search_docs — même filtre côté serveur que
        ``_search_by_reference``, sans score RRF (aucune fusion n'a eu lieu)."""
        candidates = load_chunks(self._collection, where={"ref_produit": reference})
        found = lookup_by_reference(candidates, reference)[:top_k]
        results = [
            SearchDocResult(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                rank=rank,
                title=chunk.title,
                type_doc=chunk.type_doc,
                ref_produit=chunk.ref_produit,
                version=chunk.version,
                date=chunk.date,
                source=chunk.source,
                content=chunk.content,
                rrf_score=None,
            )
            for rank, chunk in enumerate(found, start=1)
        ]
        return SearchDocsResponse(results=results, query=reference, retrieval_count=len(found))

    def search(self, question: str, top_k: int | None = None) -> SearchOutcome:
        """Point d'entrée équivalent au tool answer_question (sans la rédaction LLM,
        faite séparément par retrieval/answer.py). Route vers le lookup déterministe
        par référence si la question en contient une, sinon vers le pipeline hybride.
        """
        limit = top_k or self._settings.top_k
        reference = detect_reference(question)
        if reference is not None:
            return self._search_by_reference(reference, limit)
        return self._search_hybrid(question, limit)

    def _search_by_reference(self, reference: str, limit: int) -> SearchOutcome:
        """Lookup déterministe : Chroma filtre côté serveur (``where``), pas de scan
        du corpus complet côté Python."""
        candidates = load_chunks(self._collection, where={"ref_produit": reference})
        found = lookup_by_reference(candidates, reference)
        return SearchOutcome(
            hits=[Hit(chunk=c, rerank_score=None) for c in found[:limit]],
            is_refusal=False,  # lookup déterministe : pas de décision de pertinence
            reason=None if found else f"aucun document pour {reference}",
            route="reference",
            stages={"reference": [c.chunk_id for c in found]},
        )

    def _search_hybrid(self, question: str, limit: int) -> SearchOutcome:
        """Le pipeline complet (spec § 3) : dense + BM25 → fusion RRF → dédup de
        version + diversification → rerank → décision de refus. Les métadonnées
        nécessaires à la dédup/diversification/rerank sont rapatriées en un seul
        appel Chroma groupé, limité aux candidats retenus après la fusion — jamais
        au corpus entier.
        """
        cfg = self._settings
        dense_scored = dense_search_with_distances(
            self._collection, self._embedder, question, cfg.dense_candidates
        )
        dense = [chunk_id for chunk_id, _ in dense_scored]
        lexical_scored = self._lexical.search_with_scores(question, cfg.lexical_candidates)
        lexical = [chunk_id for chunk_id, _ in lexical_scored]
        fused_scores = rrf_scores([dense, lexical], k=cfg.rrf_k)
        fused = reciprocal_rank_fusion(
            [dense, lexical], k=cfg.rrf_k, limit=cfg.fusion_candidates
        )
        by_id = by_chunk_id(load_chunks(self._collection, ids=fused))
        versioned = keep_latest_version(fused, by_id)
        diversified = diversify(versioned, by_id)
        stages = {
            "dense": dense,
            "lexical": lexical,
            "fused": fused,
            "versioned": versioned,
            "diversified": diversified,
        }
        stage_scores = {
            "dense": dict(dense_scored),
            "lexical": dict(lexical_scored),
            "fused": {cid: fused_scores[cid] for cid in fused},
        }

        if self._reranker is None or not cfg.rerank_enabled:
            # Sans rerank, aucun score absolu : pas de décision de refus (spec § 4.3).
            hits = [Hit(chunk=by_id[cid], rerank_score=None)
                    for cid in diversified[:limit]]
            return SearchOutcome(hits=hits, is_refusal=False, route="hybrid",
                                  stages=stages, stage_scores=stage_scores)

        candidates = diversified[: cfg.rerank_candidates]
        results = self._reranker.rerank(
            question,
            [by_id[cid].content for cid in candidates],
            top_n=cfg.rerank_candidates,
        )
        reranked = [
            Hit(chunk=by_id[candidates[r.index]], rerank_score=r.score)
            for r in results
        ]
        stages["reranked"] = [h.chunk.chunk_id for h in reranked]

        best = reranked[0].rerank_score if reranked else 0.0
        if best is None or best < cfg.refusal_threshold:
            return SearchOutcome(
                hits=[],
                is_refusal=True,
                reason=(f"pertinence insuffisante : meilleur score {best:.3f} "
                        f"sous le seuil de {cfg.refusal_threshold:.2f}"),
                route="hybrid",
                stages=stages,
                stage_scores=stage_scores,
            )
        return SearchOutcome(
            hits=reranked[:limit], is_refusal=False, route="hybrid",
            stages=stages, stage_scores=stage_scores,
        )
