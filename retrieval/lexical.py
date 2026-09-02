"""Recherche lexicale BM25, index en mémoire reconstruit au démarrage."""

from rank_bm25 import BM25Okapi

from retrieval.corpus import IndexedChunk
from retrieval.tokenize import tokenize


class LexicalIndex:
    def __init__(self, chunks: list[IndexedChunk]) -> None:
        self._chunk_ids = [c.chunk_id for c in chunks]
        corpus = [tokenize(c.content) for c in chunks]
        # BM25Okapi refuse un corpus vide : on garde l'index inactif dans ce cas.
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, limit: int) -> list[str]:
        """Retourne les chunk_id classés par score BM25 décroissant."""
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self._chunk_ids[i] for i in ranked[:limit]]
