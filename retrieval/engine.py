"""Orchestration du retrieval hybride.

Chaque étape produit une liste de chunk_id ordonnée, conservée dans `stages` pour
l'affichage pédagogique de l'agent (--show-stages).
"""

from dataclasses import dataclass, field

from chromadb.api.models.Collection import Collection

from gateway.embedder import Embedder
from gateway.settings import Settings
from retrieval.corpus import IndexedChunk, by_chunk_id, load_chunks
from retrieval.dedup import diversify, keep_latest_version
from retrieval.dense import dense_search
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
        dense = dense_search(
            self._collection, self._embedder, question, cfg.dense_candidates
        )
        lexical = self._lexical.search(question, cfg.lexical_candidates)
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

        if self._reranker is None or not cfg.rerank_enabled:
            # Sans rerank, aucun score absolu : pas de décision de refus (spec § 4.3).
            hits = [Hit(chunk=self._by_id[cid], rerank_score=None)
                    for cid in diversified[:limit]]
            return SearchOutcome(hits=hits, is_refusal=False, route="hybrid", stages=stages)

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
            )
        return SearchOutcome(
            hits=reranked[:limit], is_refusal=False, route="hybrid", stages=stages
        )
