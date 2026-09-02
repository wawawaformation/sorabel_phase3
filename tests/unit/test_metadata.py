from pathlib import Path

import pytest

from ingest.errors import IngestionError
from ingest.metadata import (
    collection_of,
    diversification_group,
    document_id,
    family_id,
    type_doc_of,
)


def test_identifiants_de_base():
    p = Path("data/corpus/fiches/REF-1024-v2.1.pdf")
    assert document_id(p) == "REF-1024-v2.1"
    assert collection_of(p) == "fiches"
    assert type_doc_of("fiches") == "fiche_technique"


def test_type_doc_par_collection():
    assert type_doc_of("notices") == "notice"
    assert type_doc_of("sav") == "procedure_sav"
    assert type_doc_of("notes") == "note_interne"


def test_collection_inconnue_echoue():
    with pytest.raises(IngestionError, match="collection inconnue"):
        collection_of(Path("data/corpus/autre/x.pdf"))


def test_family_id_retire_le_suffixe_de_version():
    assert family_id("REF-1024-v2.1") == "REF-1024"
    assert family_id("notice-REF-1459-v1.1") == "notice-REF-1459"
    assert family_id("proc-casse-transport-01-v2.0") == "proc-casse-transport-01"
    # Les notes n'ont pas de version dans leur nom : inchangé.
    assert family_id("note-2024-01-11-alerte-qualite-50") == (
        "note-2024-01-11-alerte-qualite-50"
    )


def test_diversification_group():
    p = Path("x")
    # SAV et notes se regroupent par thème métier (quasi-doublons).
    assert diversification_group("sav", "proc-casse-transport-01", p) == (
        "sav_casse-transport"
    )
    assert diversification_group("notes", "note-2024-01-11-alerte-qualite-50", p) == (
        "note_alerte-qualite"
    )
    # Deux produits distincts ne sont pas des quasi-doublons : groupe = famille.
    assert diversification_group("fiches", "REF-1024", p) == "REF-1024"
    assert diversification_group("notices", "notice-REF-1459", p) == "notice-REF-1459"


def test_theme_non_derivable_echoue():
    with pytest.raises(IngestionError, match="thème"):
        diversification_group("sav", "nom-inattendu", Path("x"))
