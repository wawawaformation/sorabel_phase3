"""Orchestration du retrieval hybride.

Chaque étape produit une liste de chunk_id ordonnée, conservée dans `stages` pour
l'affichage pédagogique de l'agent (--show-stages).
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
    chunk: IndexedChunk
    rerank_score: float | None  # None si le rerank est désactivé ou hors chemin


@dataclass(frozen=True)
class SearchOutcome:
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
    rank: int  # position dans le top-k, 1-indexed
    title: str
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
        # Index BM25 reconstruit au démarrage depuis Chroma (spec § 4.6).
        self._chunks = load_chunks(collection)
        self._by_id = by_chunk_id(self._chunks)
        self._lexical = LexicalIndex(self._chunks)

    def get_document(self, document_id: str) -> IndexedChunk | None:
        """Équivalent du tool get_document : lookup direct, sans recherche.

        Les chunks sont déjà en mémoire (chargés à l'initialisation) : pas de
        nouvel appel à Chroma.
        """
        return self._by_id.get(f"{document_id}#0")

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
        un retrieval, il n'y a pas de retrieval ici.
        """
        chunks = self._chunks
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
        tools_rag_mcp.md § 2).
        """
        cfg = self._settings
        dense = dense_search(self._collection, self._embedder, query, cfg.dense_candidates)
        lexical = self._lexical.search(query, cfg.lexical_candidates)
        scores = rrf_scores([dense, lexical], k=cfg.rrf_k)
        fused = sorted(scores, key=lambda cid: scores[cid], reverse=True)
        results = [
            SearchDocResult(
                chunk_id=chunk_id,
                rank=rank,
                title=self._by_id[chunk_id].title,
                ref_produit=self._by_id[chunk_id].ref_produit,
                version=self._by_id[chunk_id].version,
                date=self._by_id[chunk_id].date,
                source=self._by_id[chunk_id].source,
                content=self._by_id[chunk_id].content,
                rrf_score=scores[chunk_id] if include_score else None,
            )
            for rank, chunk_id in enumerate(fused[:top_k], start=1)
        ]
        return SearchDocsResponse(results=results, query=query, retrieval_count=len(fused))

    def search(self, question: str, top_k: int | None = None) -> SearchOutcome:
        limit = top_k or self._settings.top_k
        reference = detect_reference(question)
        if reference is not None:
            return self._search_by_reference(reference, limit)
        return self._search_hybrid(question, limit)

    def _search_by_reference(self, reference: str, limit: int) -> SearchOutcome:
        found = lookup_by_reference(self._chunks, reference)
        return SearchOutcome(
            hits=[Hit(chunk=c, rerank_score=None) for c in found[:limit]],
            is_refusal=False,  # lookup déterministe : pas de décision de pertinence
            reason=None if found else f"aucun document pour {reference}",
            route="reference",
            stages={"reference": [c.chunk_id for c in found]},
        )

    def _search_hybrid(self, question: str, limit: int) -> SearchOutcome:
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
        versioned = keep_latest_version(fused, self._by_id)
        diversified = diversify(versioned, self._by_id)
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
            hits = [Hit(chunk=self._by_id[cid], rerank_score=None)
                    for cid in diversified[:limit]]
            return SearchOutcome(hits=hits, is_refusal=False, route="hybrid",
                                  stages=stages, stage_scores=stage_scores)

        candidates = diversified[: cfg.rerank_candidates]
        results = self._reranker.rerank(
            question,
            [self._by_id[cid].content for cid in candidates],
            top_n=cfg.rerank_candidates,
        )
        reranked = [
            Hit(chunk=self._by_id[candidates[r.index]], rerank_score=r.score)
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
