from sql.descriptions import COLUMN_DOCS, TABLE_DOCS


def test_les_cinq_tables_metier_sont_documentees():
    assert set(TABLE_DOCS) == {"produits", "stocks", "clients", "commandes", "ventes"}


def test_chaque_colonne_du_schema_a_une_description_non_vide():
    # 9 + 5 + 5 + 5 + 7 colonnes selon docs/schema.sql
    assert len(COLUMN_DOCS) == 31
    assert all(doc.description.strip() for doc in COLUMN_DOCS.values())


def test_vocabulaire_ferme_present_la_ou_il_existe():
    # Le modèle génère un meilleur SQL s'il connaît les valeurs possibles
    # (conception § 1.5) : entrepôts, statuts, segments, unités.
    assert COLUMN_DOCS[("stocks", "entrepot")].values == ("LILLE", "LYON", "NANTES")
    assert COLUMN_DOCS[("commandes", "statut")].values == (
        "en_attente", "preparee", "expediee", "livree", "annulee",
    )
    assert COLUMN_DOCS[("produits", "unite")].values == ("pièce", "conditionnement")


def test_colonnes_sans_vocabulaire_ferme_valent_none():
    assert COLUMN_DOCS[("produits", "prix_vente_ht")].values is None


def test_les_colonnes_sensibles_sont_documentees_aussi():
    # Elles sont filtrées par profil au moment de présenter le schéma (sql/schema.py),
    # pas absentes de la documentation : le profil commercial y a droit.
    assert COLUMN_DOCS[("produits", "marge_pct")].description.strip()
    assert COLUMN_DOCS[("ventes", "marge_ht")].description.strip()
