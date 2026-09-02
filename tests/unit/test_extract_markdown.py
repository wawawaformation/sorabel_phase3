from pathlib import Path

import pytest

from ingest.errors import IngestionError
from ingest.extract import extract_markdown, parse_front_matter

CORPUS = Path("data/corpus")


def test_note_interne():
    got = extract_markdown(CORPUS / "notes" / "note-2024-01-11-alerte-qualite-50.md")
    assert got.title == "Alerte qualité fournisseur"
    assert got.date == "2024-01-11"
    assert got.version == "1.0"  # '1.0' dans le fichier : guillemets retirés
    assert got.ref_produit is None
    assert "Filtech" in got.text
    assert "#" not in got.text  # syntaxe Markdown retirée


def test_front_matter_retire_les_guillemets():
    raw = "---\ntitre: T\nversion: '1.0'\ndate: 2024-01-11\n---\n\ncorps\n"
    assert parse_front_matter(raw, Path("x.md"))["version"] == "1.0"


def test_sans_front_matter_echoue(tmp_path):
    f = tmp_path / "sans.md"
    f.write_text("pas de front-matter\n", encoding="utf-8")
    with pytest.raises(IngestionError, match="front-matter"):
        extract_markdown(f)
