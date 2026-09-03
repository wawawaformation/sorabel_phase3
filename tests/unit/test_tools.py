import sqlite3
from pathlib import Path

import pytest

from sql.access import StaticAccessRules
from sql.guard import open_execution
from sql.tools import check_stock, order_status


@pytest.fixture()
def connection(tmp_path: Path) -> sqlite3.Connection:
    chemin = tmp_path / "tools.db"
    con = sqlite3.connect(chemin)
    con.executescript(
        """
        CREATE TABLE stocks (
          id INTEGER PRIMARY KEY, ref TEXT NOT NULL, entrepot TEXT NOT NULL,
          quantite INTEGER NOT NULL, seuil_reappro INTEGER NOT NULL
        );
        CREATE TABLE commandes (
          id TEXT PRIMARY KEY, client_id TEXT NOT NULL, date_commande TEXT NOT NULL,
          statut TEXT NOT NULL, montant_ht REAL NOT NULL
        );
        INSERT INTO stocks VALUES (1, 'REF-8842', 'LILLE', 247, 40);
        INSERT INTO stocks VALUES (2, 'REF-8842', 'LYON', 100, 40);
        INSERT INTO stocks VALUES (3, 'REF-8842', 'NANTES', 427, 40);
        INSERT INTO commandes VALUES ('CMD-2026-0001', 'CLI-0007', '2026-04-15', 'livree', 512.4);
        """
    )
    con.commit()
    con.close()
    return open_execution(chemin, StaticAccessRules(), "support")


def test_stock_agrege_sur_tous_les_entrepots(connection):
    resultat = check_stock(connection, "REF-8842")
    assert resultat.found is True
    assert resultat.total_quantity == 774
    assert [(w.entrepot, w.quantite) for w in resultat.by_warehouse] == [
        ("LILLE", 247), ("LYON", 100), ("NANTES", 427),
    ]


def test_stock_reference_inconnue_pas_une_erreur(connection):
    resultat = check_stock(connection, "REF-0000")
    assert resultat.found is False
    assert resultat.total_quantity == 0
    assert resultat.by_warehouse == ()


def test_stock_injection_impossible(connection):
    # Requête paramétrée : la référence est une valeur, jamais du SQL.
    resultat = check_stock(connection, "REF-8842' OR '1'='1")
    assert resultat.found is False


def test_statut_commande_trouvee(connection):
    resultat = order_status(connection, "CMD-2026-0001")
    assert resultat.found is True
    assert resultat.status == "livree"
    assert resultat.date_commande == "2026-04-15"
    assert resultat.montant_ht == 512.4


def test_commande_introuvable_retourne_found_false(connection):
    # CMD-2026-0042 n'existe pas dans la vraie base non plus : ce n'est pas une
    # erreur SQL mais une réponse légitime (conception § 4.2).
    resultat = order_status(connection, "CMD-2026-0042")
    assert resultat.found is False
    assert resultat.status is None
    assert resultat.date_commande is None
    assert resultat.montant_ht is None
