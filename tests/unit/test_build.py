from datetime import date
from pathlib import Path

import pytest

from ingest.build import build_document
from ingest.errors import IngestionError

CORPUS = Path("data/corpus")


def test_fiche():
    doc = build_document(CORPUS / "fiches" / "REF-1024-v2.1.pdf")
    assert doc.document_id == "REF-1024-v2.1"
    assert doc.family_id == "REF-1024"
    assert doc.diversification_group == "REF-1024"
    assert doc.collection == "fiches"
    assert doc.type_doc == "fiche_technique"
    assert doc.ref_produit == "REF-1024"
    assert doc.version == "2.1"
    assert doc.date == date(2022, 11, 7)
    assert doc.source == "pdf"


def test_procedure_sav():
    doc = build_document(CORPUS / "sav" / "proc-casse-transport-01-v2.0.html")
    assert doc.family_id == "proc-casse-transport-01"
    assert doc.diversification_group == "sav_casse-transport"
    assert doc.type_doc == "procedure_sav"
    assert doc.ref_produit is None
    assert doc.source == "html"


def test_note():
    doc = build_document(CORPUS / "notes" / "note-2024-01-11-alerte-qualite-50.md")
    assert doc.diversification_group == "note_alerte-qualite"
    assert doc.type_doc == "note_interne"
    assert doc.source == "md"


def test_extension_inconnue_echoue(tmp_path):
    f = tmp_path / "fiches" / "x.txt"
    f.parent.mkdir()
    f.write_text("x", encoding="utf-8")
    with pytest.raises(IngestionError, match="format non pris en charge"):
        build_document(f)
