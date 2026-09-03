import sqlite3
from pathlib import Path

import pytest

from sql.access import StaticAccessRules
from sql.schema import covered_months, read_schema, schema_as_prompt


@pytest.fixture()
def connection(tmp_path: Path) -> sqlite3.Connection:
    """Base jouet, neuve à chaque test (les tests de DDL ne doivent rien partager)."""
    chemin = tmp_path / "jouet.db"
    con = sqlite3.connect(chemin)
    con.executescript(
        """
        CREATE TABLE produits (
          ref TEXT PRIMARY KEY, nom TEXT NOT NULL, unite TEXT NOT NULL,
          prix_vente_ht REAL NOT NULL, prix_achat_ht REAL NOT NULL, marge_pct REAL NOT NULL
        );
        CREATE TABLE stocks (
          id INTEGER PRIMARY KEY, ref TEXT NOT NULL REFERENCES produits(ref),
          entrepot TEXT NOT NULL, quantite INTEGER NOT NULL, seuil_reappro INTEGER NOT NULL
        );
        CREATE TABLE commandes (
          id TEXT PRIMARY KEY, client_id TEXT NOT NULL, date_commande TEXT NOT NULL,
          statut TEXT NOT NULL, montant_ht REAL NOT NULL
        );
        INSERT INTO commandes VALUES ('CMD-2026-0001', 'CLI-0001', '2026-04-15', 'livree', 10.0);
        INSERT INTO commandes VALUES ('CMD-2025-0002', 'CLI-0002', '2025-10-03', 'livree', 20.0);
        """
    )
    con.commit()
    return con


def test_structure_lue_a_la_source_pas_codee_en_dur(connection):
    schema = read_schema(connection, StaticAccessRules(), "commercial")
    noms = [t.name for t in schema.tables]
    assert noms == ["commandes", "produits", "stocks"]  # ordre alphabétique, stable
    produits = next(t for t in schema.tables if t.name == "produits")
    assert [c.name for c in produits.columns] == [
        "ref", "nom", "unite", "prix_vente_ht", "prix_achat_ht", "marge_pct",
    ]
    assert next(c for c in produits.columns if c.name == "prix_vente_ht").type == "REAL"


def test_tables_internes_sqlite_absentes(connection):
    # sqlite_sequence et compagnie ne font pas partie du schéma métier.
    schema = read_schema(connection, StaticAccessRules(), "commercial")
    assert not any(t.name.startswith("sqlite_") for t in schema.tables)


def test_colonnes_sensibles_absentes_pour_support(connection):
    schema = read_schema(connection, StaticAccessRules(), "support")
    produits = next(t for t in schema.tables if t.name == "produits")
    noms = [c.name for c in produits.columns]
    assert "prix_vente_ht" in noms
    assert "prix_achat_ht" not in noms  # absente, pas masquée
    assert "marge_pct" not in noms


def test_colonnes_sensibles_presentes_pour_commercial(connection):
    schema = read_schema(connection, StaticAccessRules(), "commercial")
    produits = next(t for t in schema.tables if t.name == "produits")
    assert "prix_achat_ht" in [c.name for c in produits.columns]


def test_relations_lues_a_la_source(connection):
    # PRAGMA foreign_key_list expose les relations : pas besoin de les écrire à la main
    # (spec § 2.2).
    schema = read_schema(connection, StaticAccessRules(), "commercial")
    assert "stocks.ref -> produits.ref" in schema.relations


def test_descriptions_metier_jointes_a_la_structure(connection):
    schema = read_schema(connection, StaticAccessRules(), "commercial")
    stocks = next(t for t in schema.tables if t.name == "stocks")
    entrepot = next(c for c in stocks.columns if c.name == "entrepot")
    assert entrepot.description.strip()
    assert entrepot.values == ("LILLE", "LYON", "NANTES")


def test_colonne_sans_description_leve_une_erreur(tmp_path):
    # Une dérive silencieuse entre le schéma réel et sa documentation donnerait un
    # contexte incomplet au modèle : mieux vaut échouer bruyamment (spec § 4.4).
    con = sqlite3.connect(tmp_path / "derive.db")
    con.executescript("CREATE TABLE produits (ref TEXT, colonne_inconnue TEXT);")
    con.commit()
    with pytest.raises(KeyError, match="produits.colonne_inconnue"):
        read_schema(con, StaticAccessRules(), "commercial")


def test_mois_couverts_calcules_depuis_les_donnees(connection):
    # avril -> 2026 et octobre -> 2025 : une devinette « année courante » se tromperait
    # sur octobre (spec § 2.12, § 4.5).
    mois = covered_months(connection)
    assert mois["avril"] == "2026"
    assert mois["octobre"] == "2025"
    assert "mars" not in mois  # aucune commande en mars dans la base jouet


def test_mois_ambigu_exclu_de_la_correspondance(tmp_path):
    con = sqlite3.connect(tmp_path / "ambigu.db")
    con.executescript(
        """
        CREATE TABLE commandes (
          id TEXT PRIMARY KEY, client_id TEXT, date_commande TEXT, statut TEXT, montant_ht REAL
        );
        INSERT INTO commandes VALUES ('A', 'C', '2025-04-01', 'livree', 1.0);
        INSERT INTO commandes VALUES ('B', 'C', '2026-04-01', 'livree', 1.0);
        """
    )
    con.commit()
    # Deux millésimes pour avril : le mois reste légitimement ambigu, donc absent.
    assert "avril" not in covered_months(con)


def test_prompt_ne_contient_pas_les_colonnes_filtrees(connection):
    schema = read_schema(connection, StaticAccessRules(), "support")
    texte = schema_as_prompt(schema, covered_months(connection))
    assert "prix_vente_ht" in texte
    assert "prix_achat_ht" not in texte
    assert "marge_pct" not in texte
    assert "LILLE" in texte  # le vocabulaire fermé aide la génération
    assert "avril -> 2026" in texte
