"""Reciprocal Rank Fusion — combine le classement dense et le classement BM25.

RRF fusionne des classements par la position des documents, pas par leur score : un
score vectoriel (distance L2) et un score BM25 ne sont pas sur la même échelle et ne
se comparent pas directement (conception § « Recherche hybride »). Formule :
``score = Σ 1/(k+rang)`` pour chaque classement où le document apparaît — un chunk
absent d'un classement contribue 0 pour celui-ci, pas de pénalité. Un ``k`` petit fait
dominer le rang exact (être 1er compte beaucoup plus qu'être 2e) ; un ``k`` grand fait
dominer la présence dans plusieurs classements (tests/unit/test_fusion.py le démontre
avec un cas de croisement k=0 vs k=60).
"""

from collections import defaultdict

RRF_K = 60  # constante de l'article d'origine


def rrf_scores(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Calcule le score RRF de chaque chunk_id apparu dans au moins un classement.

    Retourne un dict non trié — c'est à l'appelant de trier s'il en a besoin.
    Réutilisée séparément de ``reciprocal_rank_fusion`` quand le score lui-même doit
    être exposé (search_docs avec include_score, affichage pédagogique du pipeline
    dans app.py/demo_agent.py) ; ``reciprocal_rank_fusion`` n'a besoin que du
    classement final, pas des scores intermédiaires.
    """
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1.0 / (k + rank)
    return scores


def reciprocal_rank_fusion(
    rankings: list[list[str]], k: int = RRF_K, limit: int | None = None
) -> list[str]:
    """Fusionne plusieurs classements de chunk_id (dense, BM25) en un seul classement hybride.

    Trie par score RRF décroissant puis tronque à ``limit`` si fourni. C'est cette
    fonction, pas ``rrf_scores`` directement, que ``SearchEngine._search_hybrid``
    appelle pour obtenir la liste ordonnée passée à l'étage de dédup suivant.
    """
    scores = rrf_scores(rankings, k)
    fused = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return fused[:limit] if limit is not None else fused
