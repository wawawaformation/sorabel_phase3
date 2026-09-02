"""Découpage en chunks.

Sur ce corpus, 1 chunk = 1 document entier (documents très en-deçà d'une taille
justifiant un découpage) : chunk_index vaut toujours 0. Le découpage structurel
décrit en conception reste un repli non implémenté — un découpeur que rien ne
déclenche serait du code mort.
"""

from ingest.chunk import Chunk
from ingest.document import DocumentCanonique


def to_chunks(doc: DocumentCanonique) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=f"{doc.document_id}#0",
            document_id=doc.document_id,
            chunk_index=0,
            family_id=doc.family_id,
            diversification_group=doc.diversification_group,
            content=doc.content,
            title=doc.title,
            type_doc=doc.type_doc,
            collection=doc.collection,
            ref_produit=doc.ref_produit,
            version=doc.version,
            date=doc.date,
            source=doc.source,
        )
    ]
