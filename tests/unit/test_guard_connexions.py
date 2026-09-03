import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

from sql.access import StaticAccessRules
from sql.guard import open_execution, open_introspection

SCHEMA = """
CREATE TABLE produits (
  ref TEXT PRIMARY KEY, nom TEXT NOT NULL,
  prix_vente_ht REAL NOT NULL, prix_achat_ht REAL NOT NULL, marge_pct REAL NOT NULL
);
CREATE TABLE ventes (
  id INTEGER PRIMARY KEY, ref TEXT NOT NULL, quantite INTEGER NOT NULL,
  marge_ht REAL NOT NULL
);
INSERT INTO produits VALUES ('REF-8842', 'Disjoncteur', 42.0, 21.0, 50.0);
INSERT INTO ventes VALUES (1, 'REF-8842', 3, 12.5);
"""


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Base neuve par test : indispensable, certains tests exécutent du DDL."""
    chemin = tmp_path / "guard.db"
    con = sqlite3.connect(chemin)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return chemin


def test_introspection_autorise_pragma(db_path):
    con = open_introspection(db_path)
    assert con.execute("PRAGMA table_info(produits)").fetchall()


def test_introspection_refuse_l_ecriture(db_path):
    # mode=ro : même sans authorizer, aucune écriture possible.
    con = open_introspection(db_path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("UPDATE produits SET nom='X'")


def test_execution_autorise_les_lectures_legitimes(db_path):
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    assert con.execute("SELECT ref, nom FROM produits").fetchall()
    assert con.execute("SELECT SUM(quantite) FROM ventes").fetchone()[0] == 3
    assert con.execute(
        "SELECT p.nom, SUM(v.quantite) FROM ventes v JOIN produits p ON p.ref = v.ref "
        "GROUP BY p.nom"
    ).fetchall()


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO produits VALUES ('X', 'Y', 1.0, 1.0, 1.0)",
        "UPDATE produits SET nom='X' WHERE ref='REF-8842'",
        "DELETE FROM ventes",
        "DROP TABLE ventes",
        "CREATE TABLE t (a INT)",
        "CREATE VIEW v AS SELECT ref FROM produits",
        "ALTER TABLE produits ADD COLUMN x TEXT",
        "ATTACH DATABASE ':memory:' AS autre",
        "PRAGMA table_info(produits)",
    ],
)
def test_execution_refuse_tout_ce_qui_n_est_pas_lecture(db_path, sql):
    # Allowlist, deny par défaut : une liste noire laisserait passer ce qu'on a oublié
    # d'énumérer — vérifié, un authorizer qui ne filtre que des colonnes laisse
    # passer les UPDATE (spec § 2.3).
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute(sql)


def test_execution_refuse_les_tables_internes_sqlite(db_path):
    # Fuite réelle : sans cette règle, le CREATE TABLE complet est lisible et révèle
    # l'existence des colonnes sensibles (spec § 2.4).
    con = open_execution(db_path, StaticAccessRules(), "support")
    with pytest.raises(sqlite3.DatabaseError, match="sqlite_master"):
        con.execute("SELECT name, sql FROM sqlite_master")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT marge_pct FROM produits",
        "SELECT ref FROM produits ORDER BY marge_pct",
        "SELECT AVG(marge_pct) FROM produits",
        "SELECT * FROM produits",
        "SELECT marge_ht FROM ventes",
    ],
)
def test_colonne_sensible_refusee_pour_support_ou_qu_elle_soit(db_path, sql):
    con = open_execution(db_path, StaticAccessRules(), "support")
    with pytest.raises(sqlite3.DatabaseError, match="prohibited"):
        con.execute(sql)


def test_colonne_sensible_lisible_par_commercial(db_path):
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    assert con.execute("SELECT marge_pct FROM produits").fetchone()[0] == 50.0


def test_plusieurs_instructions_refusees_par_le_driver(db_path):
    # Protection gratuite : le driver Python refuse avant même SQLite (spec § 2.7).
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    with pytest.raises(sqlite3.ProgrammingError):
        con.execute("SELECT 1; SELECT 2")


def test_le_fichier_reste_intact_apres_toutes_ces_tentatives(db_path, tmp_path):
    reference = tmp_path / "reference.db"
    shutil.copy(db_path, reference)
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    for sql in ("DELETE FROM ventes", "DROP TABLE produits"):
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(sql)
    con.close()
    assert db_path.read_bytes() == reference.read_bytes()


def _run_in_thread(fn) -> Exception | None:
    """Exécute fn() dans un thread séparé, retourne l'exception levée (ou None)."""
    erreur: list[Exception] = []

    def cible() -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — on veut capturer n'importe quelle exception
            erreur.append(exc)

    thread = threading.Thread(target=cible)
    thread.start()
    thread.join()
    return erreur[0] if erreur else None


def test_connexion_execution_utilisable_depuis_un_autre_thread(db_path):
    # Reproduit le bug observé avec Streamlit : @st.cache_resource construit le moteur
    # dans un thread, un rerun ultérieur peut réutiliser la connexion depuis un autre
    # thread. Par défaut, sqlite3 refuse ("SQLite objects created in a thread can only
    # be used in that same thread") — check_same_thread=False lève cette restriction,
    # sûr ici car les connexions sont en lecture seule et l'accès reste séquentiel,
    # jamais concurrent (pas de vrai partage simultané entre threads).
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    erreur = _run_in_thread(lambda: con.execute("SELECT ref, nom FROM produits").fetchall())
    assert erreur is None, f"utilisable depuis un autre thread : {erreur}"


def test_connexion_introspection_utilisable_depuis_un_autre_thread(db_path):
    con = open_introspection(db_path)
    erreur = _run_in_thread(lambda: con.execute("PRAGMA table_info(produits)").fetchall())
    assert erreur is None, f"utilisable depuis un autre thread : {erreur}"
