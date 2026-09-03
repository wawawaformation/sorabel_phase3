import sqlite3
from pathlib import Path

import pytest

from sql.access import StaticAccessRules
from sql.guard import ValidationError, apply_limit, open_execution, run_query, validate_sql


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    chemin = tmp_path / "validation.db"
    con = sqlite3.connect(chemin)
    con.executescript(
        "CREATE TABLE ventes (id INTEGER PRIMARY KEY, quantite INTEGER NOT NULL);"
        + "".join(f"INSERT INTO ventes VALUES ({i}, {i});" for i in range(1, 251))
    )
    con.commit()
    con.close()
    return chemin


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT ref FROM produits",
        "select ref from produits",
        "  SELECT ref FROM produits  ",
        "WITH t AS (SELECT ref FROM produits) SELECT ref FROM t",
        "SELECT ref FROM produits;",
    ],
)
def test_formes_de_lecture_acceptees(sql):
    validate_sql(sql)  # ne lève pas


@pytest.mark.parametrize(
    ("sql", "motif"),
    [
        ("INSERT INTO produits VALUES ('X')", "écriture"),
        ("UPDATE produits SET nom='X'", "écriture"),
        ("DELETE FROM ventes", "écriture"),
        ("DROP TABLE ventes", "écriture"),
        ("ALTER TABLE produits ADD COLUMN x TEXT", "écriture"),
        ("CREATE TABLE t (a INT)", "écriture"),
        ("REPLACE INTO produits VALUES ('X')", "écriture"),
        ("ATTACH DATABASE 'x.db' AS x", "écriture"),
        ("SELECT ref FROM produits; DROP TABLE ventes", "une seule instruction"),
        ("SELECT * FROM produits", "SELECT \\*"),
        ("select  *  from produits", "SELECT \\*"),
        ("", "vide"),
    ],
)
def test_formes_refusees(sql, motif):
    with pytest.raises(ValidationError, match=motif):
        validate_sql(sql)


def test_select_etoile_refuse_meme_pour_commercial():
    # L'authorizer ne le bloque pas pour un profil sans colonne interdite : la règle
    # garde donc son utilité, pour la lisibilité de la trace (spec § 4.3).
    with pytest.raises(ValidationError, match="SELECT \\*"):
        validate_sql("SELECT * FROM produits")


def test_limit_ajoute_quand_absent():
    assert apply_limit("SELECT id FROM ventes", 100) == "SELECT id FROM ventes LIMIT 101"


def test_limit_existant_respecte():
    sql = "SELECT id FROM ventes LIMIT 5"
    assert apply_limit(sql, 100) == sql


def test_limit_non_ajoute_sur_une_agregation():
    # Un COUNT/SUM retourne naturellement une ligne : un LIMIT n'apporterait rien
    # (conception § 2.7).
    sql = "SELECT COUNT(*) FROM ventes"
    assert apply_limit(sql, 100) == sql


def test_point_virgule_final_gere():
    assert apply_limit("SELECT id FROM ventes;", 100) == "SELECT id FROM ventes LIMIT 101"


def test_resultat_tronque_signale(db_path):
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    colonnes, lignes, tronque = run_query(con, "SELECT id FROM ventes", 5.0, 100)
    assert colonnes == ["id"]
    assert len(lignes) == 100  # la 101e ligne interrogée n'est pas retournée
    assert tronque is True


def test_resultat_complet_non_signale_comme_tronque(db_path):
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    _, lignes, tronque = run_query(con, "SELECT id FROM ventes WHERE id <= 10", 5.0, 100)
    assert len(lignes) == 10
    assert tronque is False


def test_exactement_la_limite_n_est_pas_une_troncature(db_path):
    # Le cas piège que LIMIT+1 résout : 100 lignes reçues ne dit pas s'il y en avait
    # 100 ou 993 (spec § 2.10).
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    _, lignes, tronque = run_query(con, "SELECT id FROM ventes WHERE id <= 100", 5.0, 100)
    assert len(lignes) == 100
    assert tronque is False


def test_requete_trop_longue_interrompue(db_path):
    # set_progress_handler, et non busy_timeout qui ne gère que la contention de
    # verrous (vérifié en conception § 2.7).
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        run_query(
            con,
            "SELECT COUNT(*) FROM ventes v1, ventes v2, ventes v3, ventes v4",
            0.2,
            100,
        )
