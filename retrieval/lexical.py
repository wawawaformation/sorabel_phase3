"""Recherche lexicale BM25 (retrieval/fusion.py la combine à la recherche dense).

Index en mémoire reconstruit au démarrage à partir des chunks déjà chargés depuis
Chroma (retrieval/corpus.py) : ``rank_bm25`` ne persiste rien sur disque, donc l'index
disparaît à l'arrêt du process et se reconstruit à chaque redémarrage de
``SearchEngine`` — un coût négligeable sur 400 chunks (spec § 2.5).
"""

from rank_bm25 import BM25Okapi

from retrieval.corpus import IndexedChunk
from retrieval.tokenize import tokenize


class LexicalIndex:
    """Enveloppe autour de ``BM25Okapi`` qui garde la correspondance rang → chunk_id.

    ``rank_bm25`` travaille par position dans le corpus (des entiers), pas par
    chunk_id ; cette classe fait la traduction dans les deux sens.
    """

    def __init__(self, chunks: list[IndexedChunk]) -> None:
        self._chunk_ids = [c.chunk_id for c in chunks]
        corpus = [tokenize(c.content) for c in chunks]
        # BM25Okapi refuse un corpus vide : on garde l'index inactif dans ce cas.
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def search_with_scores(self, query: str, limit: int) -> list[tuple[str, float]]:
        """Retourne (chunk_id, score BM25), score décroissant — plus haut = meilleur."""
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [(self._chunk_ids[i], float(scores[i])) for i in ranked[:limit]]

    def search(self, query: str, limit: int) -> list[str]:
        """Retourne les chunk_id classés par score BM25 décroissant."""
        return [chunk_id for chunk_id, _ in self.search_with_scores(query, limit)]
