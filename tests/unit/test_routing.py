from retrieval.corpus import IndexedChunk
from retrieval.routing import detect_reference, lookup_by_reference


def _chunk(chunk_id: str, ref: str | None, version: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=chunk_id, document_id=chunk_id.split("#")[0], content="c", title="T",
        type_doc="fiche_technique", collection="fiches", version=version,
        date="2024-01-01", source="pdf", family_id=(ref or "fam"),
        diversification_group=(ref or "grp"), ref_produit=ref,
    )


CHUNKS = [
    _chunk("REF-8842-v1.0#0", "REF-8842", "1.0"),
    _chunk("REF-8842-v2.1#0", "REF-8842", "2.1"),
    _chunk("REF-1024-v1.0#0", "REF-1024", "1.0"),
    _chunk("proc-01-v1.0#0", None, "1.0"),
]


def test_detection_reference_seule():
    assert detect_reference("REF-8842") == "REF-8842"


def test_detection_reference_dans_une_phrase():
    assert detect_reference("fiche technique REF-8842") == "REF-8842"
    assert detect_reference("ref-8842 svp") == "REF-8842"  # casse indifférente


def test_pas_de_reference():
    assert detect_reference("que faire si un colis arrive endommagé ?") is None


def test_lookup_retourne_la_derniere_version_en_tete():
    got = lookup_by_reference(CHUNKS, "REF-8842")
    assert [c.chunk_id for c in got] == ["REF-8842-v2.1#0", "REF-8842-v1.0#0"]


def test_lookup_reference_absente():
    assert lookup_by_reference(CHUNKS, "REF-0000") == []
