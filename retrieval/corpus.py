"""Chargement des chunks indexés depuis Chroma.

Les 400 chunks tiennent en mémoire : l'index BM25 se reconstruit au démarrage, rien
n'est persisté (spec § 4.6).
"""

from dataclasses import dataclass

from chromadb.api.models.Collection import Collection


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    document_id: str
    content: str
    title: str
    type_doc: str
    collection: str
    version: str
    date: str  # ISO, tel que stocké dans Chroma
    source: str
    family_id: str
    diversification_group: str
    ref_produit: str | None


def load_chunks(collection: Collection) -> list[IndexedChunk]:
    got = collection.get(include=["documents", "metadatas"])  # type: ignore[list-item]
    chunks: list[IndexedChunk] = []
    for chunk_id, content, meta in zip(
        got["ids"], got["documents"] or [], got["metadatas"] or [], strict=True
    ):
        chunks.append(
            IndexedChunk(
                chunk_id=chunk_id,
                document_id=str(meta["document_id"]),
                content=content,
                title=str(meta["title"]),
                type_doc=str(meta["type_doc"]),
                collection=str(meta["collection"]),
                version=str(meta["version"]),
                date=str(meta["date"]),
                source=str(meta["source"]),
                family_id=str(meta["family_id"]),
                diversification_group=str(meta["diversification_group"]),
                # clé absente pour sav/ et notes/ : Chroma refuse les valeurs None
                ref_produit=str(meta["ref_produit"]) if "ref_produit" in meta else None,
            )
        )
    return chunks


def by_chunk_id(chunks: list[IndexedChunk]) -> dict[str, IndexedChunk]:
    return {c.chunk_id: c for c in chunks}
