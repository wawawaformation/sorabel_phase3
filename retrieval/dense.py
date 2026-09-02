"""Recherche dense : embedding de la question, puis plus proches voisins dans Chroma."""

from chromadb.api.models.Collection import Collection

from gateway.embedder import Embedder


def dense_search(
    collection: Collection, embedder: Embedder, query: str, limit: int
) -> list[str]:
    """Retourne les chunk_id classés du plus proche au plus lointain."""
    vector = embedder.embed([query])[0]
    result = collection.query(query_embeddings=[vector], n_results=limit)
    ids = result["ids"]
    return list(ids[0]) if ids else []
