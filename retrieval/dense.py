"""Recherche dense : embedding de la question, puis plus proches voisins dans Chroma."""

from chromadb.api.models.Collection import Collection

from gateway.embedder import Embedder


def dense_search_with_distances(
    collection: Collection, embedder: Embedder, query: str, limit: int
) -> list[tuple[str, float]]:
    """Retourne (chunk_id, distance), du plus proche au plus lointain.

    Distance L2 (défaut Chroma, aucun espace vectoriel configuré à la création de la
    collection — vérifié : col.metadata est vide) : plus BAS = plus proche, à
    l'inverse de BM25/RRF/rerank où plus haut est meilleur.
    """
    vector = embedder.embed([query])[0]
    result = collection.query(
        query_embeddings=[vector],  # type: ignore[arg-type]
        n_results=limit,
        include=["distances"],  # type: ignore[list-item]
    )
    ids = result["ids"][0] if result["ids"] else []
    distances_by_batch = result["distances"]
    distances = distances_by_batch[0] if distances_by_batch else [0.0] * len(ids)
    return list(zip(ids, distances, strict=True))


def dense_search(
    collection: Collection, embedder: Embedder, query: str, limit: int
) -> list[str]:
    """Retourne les chunk_id classés du plus proche au plus lointain."""
    return [chunk_id for chunk_id, _ in dense_search_with_distances(collection, embedder, query, limit)]
