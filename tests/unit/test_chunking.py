from datetime import date

from ingest.chunking import to_chunks
from ingest.document import DocumentCanonique


def _doc() -> DocumentCanonique:
    return DocumentCanonique(
        document_id="REF-1024-v2.1",
        family_id="REF-1024",
        diversification_group="REF-1024",
        content="contenu",
        title="Disjoncteur",
        type_doc="fiche_technique",
        collection="fiches",
        ref_produit="REF-1024",
        version="2.1",
        date=date(2022, 11, 7),
        source="pdf",
    )


def test_un_document_donne_un_chunk():
    chunks = to_chunks(_doc())
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_id == "REF-1024-v2.1#0"
    assert chunk.chunk_index == 0
    assert chunk.document_id == "REF-1024-v2.1"


def test_metadonnees_heritees_du_document():
    chunk = to_chunks(_doc())[0]
    doc = _doc()
    for field in (
        "family_id", "diversification_group", "content", "title",
        "type_doc", "collection", "ref_produit", "version", "date", "source",
    ):
        assert getattr(chunk, field) == getattr(doc, field)
