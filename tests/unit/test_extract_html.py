from pathlib import Path

from ingest.extract import extract_html

CORPUS = Path("data/corpus")


def test_procedure_sav():
    got = extract_html(CORPUS / "sav" / "proc-casse-transport-01-v2.0.html")
    assert got.title == (
        "Procédure SAV — Colis reçu endommagé : constat et prise en charge (01)"
    )
    assert got.version == "2.0"
    assert got.date == "2026-04-05"
    # Les procédures SAV sont génériques : la référence citée n'est qu'un exemple.
    assert got.ref_produit is None
    assert "Conditions" in got.text
    assert "<" not in got.text  # balises retirées
