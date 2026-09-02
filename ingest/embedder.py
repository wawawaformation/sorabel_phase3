"""Texte servant au vecteur dense d'un chunk. L'embedder lui-même est dans gateway/."""

from ingest.chunk import Chunk


def embedding_text(chunk: Chunk) -> str:
    """Texte servant au vecteur dense : title + ref_produit + content.

    Aide le matching sémantique quand la requête ne contient pas de REF-xxxx
    explicite. Le content stocké et retourné, lui, n'est jamais modifié.
    """
    parts = [chunk.title]
    if chunk.ref_produit:
        parts.append(chunk.ref_produit)
    parts.append(chunk.content)
    return " ".join(parts)
