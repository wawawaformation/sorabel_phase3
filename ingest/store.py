"""Écriture des chunks dans Chroma.

Contraintes vérifiées sur chromadb 0.5 : une valeur de métadonnée doit être
str/int/float/bool ; None et les listes sont refusés (ValueError). Une clé absente
est la seule façon d'exprimer « pas de valeur ».
"""

from urllib.parse import urlparse

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from ingest.chunk import Chunk
from ingest.settings import Settings


def chroma_metadata(chunk: Chunk) -> dict[str, str | int]:
    meta: dict[str, str | int] = {
        "document_id": chunk.document_id,
        "chunk_index": chunk.chunk_index,
        "family_id": chunk.family_id,
        "diversification_group": chunk.diversification_group,
        "title": chunk.title,
        "type_doc": chunk.type_doc,
        "collection": chunk.collection,
        "version": chunk.version,
        "date": chunk.date.isoformat(),
        "source": chunk.source,
    }
    # ref_produit est omise (jamais None) pour sav/ et notes/.
    if chunk.ref_produit:
        meta["ref_produit"] = chunk.ref_produit
    return meta


def chroma_client(settings: Settings) -> ClientAPI:
    parsed = urlparse(settings.chroma_url)
    return chromadb.HttpClient(
        host=parsed.hostname or "localhost", port=parsed.port or 8000
    )


def open_collection(client: ClientAPI, name: str) -> Collection:
    """Ouvre ou crée la collection, sans fonction d'embedding.

    Aucune embedding_function n'est passée : les vecteurs sont toujours fournis
    explicitement. Laisser Chroma calculer déclencherait le téléchargement de son
    modèle ONNX par défaut.
    """
    return client.get_or_create_collection(name=name, embedding_function=None)


def upsert_chunks(
    collection: Collection, chunks: list[Chunk], vectors: list[list[float]]
) -> None:
    if len(chunks) != len(vectors):
        raise ValueError(f"{len(chunks)} chunks pour {len(vectors)} vecteurs")
    if not chunks:
        return
    collection.upsert(
        ids=[c.chunk_id for c in chunks],
        embeddings=vectors,  # type: ignore[arg-type]
        documents=[c.content for c in chunks],
        metadatas=[chroma_metadata(c) for c in chunks],  # type: ignore[arg-type]
    )
