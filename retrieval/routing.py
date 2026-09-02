"""Routing des références exactes — étape 0 du pipeline, court-circuite le retrieval.

Si la question porte une REF-nnnn, on ne cherche pas : on connaît la clé. Lookup
déterministe par métadonnée, garantie à 100 % là où un bon classement (dense/BM25/RRF)
ne l'est jamais complètement (conception § « remplacé par un routing côté client »).
``SearchEngine.search`` appelle ``detect_reference`` en tout premier ; si elle trouve
une référence, ``_search_hybrid`` n'est jamais exécuté pour cette question.
"""

import re

from retrieval.corpus import IndexedChunk
from retrieval.dedup import version_key

RE_REFERENCE = re.compile(r"\bREF-(\d{4})\b", re.IGNORECASE)


def detect_reference(question: str) -> str | None:
    """Cherche un motif REF-nnnn (4 chiffres) dans la question, insensible à la casse.

    Retourne la référence normalisée en majuscules (« REF-1024 »), ou ``None`` si
    aucune référence n'est présente — auquel cas le retrieval hybride prend le relais.
    """
    match = RE_REFERENCE.search(question)
    return f"REF-{match.group(1)}" if match else None


def lookup_by_reference(
    chunks: list[IndexedChunk], reference: str
) -> list[IndexedChunk]:
    """Retourne tous les chunks portant cette ref_produit, triés version décroissante.

    Une référence produit peut couvrir plusieurs documents logiques (une fiche
    technique et une notice partagent parfois la même référence, spec § 2.3) : la
    liste peut donc contenir plusieurs family_id différents, chacun avec ses propres
    versions. Aucun filtrage de version ici — contrairement à retrieval/dedup.py, on
    garde volontairement tout ce qui correspond, l'appelant limite ensuite à ``limit``.
    """
    found = [c for c in chunks if c.ref_produit == reference]
    return sorted(found, key=lambda c: version_key(c.version), reverse=True)
