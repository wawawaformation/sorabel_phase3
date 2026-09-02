"""Étages 4 et 5 du pipeline hybride : deux filtres anti-doublons distincts et
complémentaires, tous deux mesurés nécessaires sur le corpus réel (spec § 2.3) — un
top-3 BM25 naïf a montré 3 fois le même titre avant leur ajout.

- ``keep_latest_version`` : plusieurs versions d'un même document logique
  (``family_id``, ex. la procédure « colis endommagé » en v1.0 et v2.0) — on ne garde
  que la plus récente, quel que soit son rang dans le classement fusionné (on ne veut
  jamais présenter une notice obsolète alors qu'une version à jour existe).
- ``diversify`` : plusieurs documents distincts mais thématiquement proches
  (``diversification_group``) — on n'en garde qu'un, le mieux classé, pour que le
  top-k final ne soit pas noyé par des quasi-doublons sur un seul thème.

Les deux fonctions opèrent uniquement sur des listes de chunk_id (l'ordre porte le
classement) et un dict de lookup ``by_id`` — jamais sur les scores de fusion, qui ont
déjà servi à produire ce classement en amont.
"""

from retrieval.corpus import IndexedChunk


def version_key(version: str) -> tuple[int, ...]:
    """Convertit une chaîne de version (« 2.1 ») en tuple comparable numériquement.

    Une comparaison de chaînes échouerait sur « 1.10 » vs « 1.9 » (« 1.10 » < « 1.9 »
    lexicographiquement) ; le tuple d'entiers (1, 10) > (1, 9) donne le bon ordre.
    Un segment non numérique (rare, absent du corpus actuel) vaut 0.
    """
    parts = []
    for part in version.split("."):
        parts.append(int(part) if part.isdigit() else 0)
    return tuple(parts)


def keep_latest_version(
    chunk_ids: list[str], by_id: dict[str, IndexedChunk]
) -> list[str]:
    """Réduit le classement à une seule entrée par family_id : la version la plus récente.

    Le rang de sortie est celui de la première apparition de la famille dans le
    classement d'entrée, pas celui de la version retenue — si la v1.0 apparaît en
    rang 2 et la v2.0 en rang 5, la v2.0 est gardée mais à la position de la v1.0.
    Ce choix évite qu'une famille ne recule dans le classement simplement parce que
    sa version la plus récente y figure plus bas.
    """
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
    """Ne garde qu'un seul représentant par diversification_group : le mieux classé.

    Parcourt le classement dans l'ordre et ignore tout chunk dont le groupe a déjà
    été vu — le premier chunk d'un groupe rencontré est donc toujours le mieux classé
    pour ce groupe, puisque l'entrée est déjà triée par pertinence.
    """
    seen: set[str] = set()
    kept: list[str] = []
    for chunk_id in chunk_ids:
        group = by_id[chunk_id].diversification_group
        if group in seen:
            continue
        seen.add(group)
        kept.append(chunk_id)
    return kept
