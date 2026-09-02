"""Routing des références exactes, hors du retrieval.

Si la question porte une REF-nnnn, on ne cherche pas : on connaît la clé. Lookup
déterministe par métadonnée, garantie à 100 % là où un bon classement ne l'est pas
(conception § « remplacé par un routing côté client »).
"""

import re

from retrieval.corpus import IndexedChunk
from retrieval.dedup import version_key

RE_REFERENCE = re.compile(r"\bREF-(\d{4})\b", re.IGNORECASE)


def detect_reference(question: str) -> str | None:
    """Retourne la référence normalisée (REF-nnnn) si la question en contient une."""
    match = RE_REFERENCE.search(question)
    return f"REF-{match.group(1)}" if match else None


def lookup_by_reference(
    chunks: list[IndexedChunk], reference: str
) -> list[IndexedChunk]:
    """Chunks portant cette ref_produit, la version la plus récente en tête."""
    found = [c for c in chunks if c.ref_produit == reference]
    return sorted(found, key=lambda c: version_key(c.version), reverse=True)
