"""Deux filtres distincts, mesurés nécessaires sur le corpus réel (spec § 2.3).

- keep_latest_version : plusieurs versions d'un même document logique (family_id) —
  on retourne la plus récente, pas la mieux classée.
- diversify : plusieurs documents voisins d'un même thème (diversification_group) —
  on n'en garde qu'un, pour ne pas remplir le top-k de quasi-doublons.
"""

from retrieval.corpus import IndexedChunk


def version_key(version: str) -> tuple[int, ...]:
    """« 2.1 » → (2, 1). Comparaison numérique : « 1.10 » est postérieure à « 1.9 »."""
    parts = []
    for part in version.split("."):
        parts.append(int(part) if part.isdigit() else 0)
    return tuple(parts)


def keep_latest_version(
    chunk_ids: list[str], by_id: dict[str, IndexedChunk]
) -> list[str]:
    """Une seule entrée par family_id : la version la plus récente, au meilleur rang."""
    best: dict[str, str] = {}
    order: list[str] = []
    for chunk_id in chunk_ids:
        family = by_id[chunk_id].family_id
        if family not in best:
            best[family] = chunk_id
            order.append(family)
            continue
        current = by_id[best[family]]
        if version_key(by_id[chunk_id].version) > version_key(current.version):
            best[family] = chunk_id
    return [best[family] for family in order]


def diversify(chunk_ids: list[str], by_id: dict[str, IndexedChunk]) -> list[str]:
    """Un seul représentant par diversification_group, le mieux classé."""
    seen: set[str] = set()
    kept: list[str] = []
    for chunk_id in chunk_ids:
        group = by_id[chunk_id].diversification_group
        if group in seen:
            continue
        seen.add(group)
        kept.append(chunk_id)
    return kept
