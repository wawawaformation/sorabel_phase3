from pathlib import Path

import pytest

from ingest.errors import IngestionError
from ingest.extract import extract_pdf

CORPUS = Path("data/corpus")


def test_fiche_technique():
    got = extract_pdf(CORPUS / "fiches" / "REF-1024-v2.1.pdf")
    assert got.title == "Disjoncteur triphasé 63 A courbe D"
    assert got.ref_produit == "REF-1024"
    assert got.version == "2.1"
    assert got.date == "2022-11-07"
    assert "Pouvoir de coupure" in got.text


def test_notice_meme_ligne():
    # Dans une notice, référence, version et date sont sur la MÊME ligne :
    # l'extraction ne doit pas dépendre de la position des lignes.
    got = extract_pdf(CORPUS / "notices" / "notice-REF-1459-v1.1.pdf")
    assert got.title == "Projecteur led 30 W rechargeable"
    assert got.ref_produit == "REF-1459"
    assert got.version == "1.1"
    assert got.date == "2024-03-23"


def test_champ_manquant_echoue(tmp_path):
    from pypdf import PdfWriter

    vide = tmp_path / "vide.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with vide.open("wb") as fh:
        writer.write(fh)

    with pytest.raises(IngestionError, match="title"):
        extract_pdf(vide)
