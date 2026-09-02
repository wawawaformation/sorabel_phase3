"""Reciprocal Rank Fusion.

RRF fusionne des classements par la position des documents, pas par leur score : un
score vectoriel et un score BM25 ne sont pas sur la même échelle et ne se comparent pas
directement (conception § « Recherche hybride »).
"""

from collections import defaultdict

RRF_K = 60  # constante de l'article d'origine


def rrf_scores(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Score RRF de chaque chunk_id, non trié. Réutilisé quand le score doit être exposé
    (search_docs, include_score) — reciprocal_rank_fusion() n'en a pas besoin, seul le
    classement compte pour le pipeline hybride."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1.0 / (k + rank)
    return scores


def reciprocal_rank_fusion(
    rankings: list[list[str]], k: int = RRF_K, limit: int | None = None
) -> list[str]:
    """Fusionne des classements de chunk_id ; retourne le classement hybride."""
    scores = rrf_scores(rankings, k)
    fused = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return fused[:limit] if limit is not None else fused
