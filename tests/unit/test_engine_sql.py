import json
import sqlite3
from pathlib import Path

import pytest

from gateway.settings import Settings
from sql.access import StaticAccessRules
from sql.engine import SqlEngine
from sql.trace import JsonlTraceRecorder

SCHEMA = """
CREATE TABLE produits (
  ref TEXT PRIMARY KEY, nom TEXT NOT NULL, categorie TEXT NOT NULL,
  fabricant TEXT NOT NULL, unite TEXT NOT NULL, prix_vente_ht REAL NOT NULL,
  prix_achat_ht REAL NOT NULL, marge_pct REAL NOT NULL, actif INTEGER NOT NULL
);
CREATE TABLE stocks (
  id INTEGER PRIMARY KEY, ref TEXT NOT NULL REFERENCES produits(ref),
  entrepot TEXT NOT NULL, quantite INTEGER NOT NULL, seuil_reappro INTEGER NOT NULL
);
CREATE TABLE clients (
  id TEXT PRIMARY KEY, raison_sociale TEXT NOT NULL, segment TEXT NOT NULL,
  ville TEXT NOT NULL, email TEXT NOT NULL
);
CREATE TABLE commandes (
  id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id),
  date_commande TEXT NOT NULL, statut TEXT NOT NULL, montant_ht REAL NOT NULL
);
CREATE TABLE ventes (
  id INTEGER PRIMARY KEY, commande_id TEXT NOT NULL REFERENCES commandes(id),
  ref TEXT NOT NULL REFERENCES produits(ref), quantite INTEGER NOT NULL,
  prix_unitaire_ht REAL NOT NULL, remise_pct REAL NOT NULL, marge_ht REAL NOT NULL
);
INSERT INTO produits VALUES ('REF-8842', 'Disjoncteur', 'Protection électrique',
  'Voltane', 'pièce', 42.0, 21.0, 50.0, 1);
INSERT INTO stocks VALUES (1, 'REF-8842', 'LILLE', 247, 40);
INSERT INTO stocks VALUES (2, 'REF-8842', 'LYON', 100, 40);
INSERT INTO clients VALUES ('CLI-0007', 'Elec Nord', 'PME', 'Lille', 'a@b.c');
INSERT INTO commandes VALUES ('CMD-2026-0001', 'CLI-0007', '2026-04-15', 'livree', 512.4);
INSERT INTO ventes VALUES (1, 'CMD-2026-0001', 'REF-8842', 3, 40.0, 5.0, 12.5);
"""


class FakeLLM:
    """Retourne une charge JSON fixée par le test, et mémorise le prompt reçu."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.last_system = ""
        engine = self

        class _Completions:
            def create(self, **kwargs):
                engine.last_system = kwargs["messages"][0]["content"]

                class _M:
                    content = json.dumps(engine.payload)

                class _C:
                    message = _M()

                class _R:
                    choices = [_C()]

                return _R()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _payload(**overrides) -> dict:
    base = {
        "status": "SQL_GENERABLE",
        "sql": "SELECT SUM(quantite) FROM stocks WHERE ref = 'REF-8842'",
        "tables_referencees": ["stocks"],
        "colonnes_referencees": ["stocks.ref", "stocks.quantite"],
        "clarification": "",
        "reason": "",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    chemin = tmp_path / "engine.db"
    con = sqlite3.connect(chemin)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return chemin


def _engine(
    db_path: Path, tmp_path: Path, payload: dict, profile: str = "commercial",
    llm: FakeLLM | None = None,
) -> SqlEngine:
    """Moteur de test. ``llm`` permet au test de garder sa propre référence au double,
    plutôt que d'aller lire un attribut privé du moteur pour l'inspecter."""
    settings = Settings(_env_file=None).model_copy(update={
        "sqlite_path": db_path,
        "sql_alert_log": tmp_path / "alerte.jsonl",
    })
    trace = JsonlTraceRecorder(tmp_path / "audit.jsonl", tmp_path / "alerte.jsonl")
    return SqlEngine(
        profile=profile,
        access_rules=StaticAccessRules(),
        trace=trace,
        llm_client=llm or FakeLLM(payload),
        settings=settings,
    )


def _journal(tmp_path: Path) -> list[dict]:
    chemin = tmp_path / "audit.jsonl"
    if not chemin.exists():
        return []
    return [json.loads(x) for x in chemin.read_text("utf-8").splitlines() if x.strip()]


def test_get_schema_appelable_par_les_deux_profils(db_path, tmp_path):
    # La conception fait autorité contre tests/conftest.py, qui masque get_schema au
    # profil support : ici l'appel réussit, seul le contenu diffère (spec § 8 point 3).
    for profil in ("support", "commercial"):
        schema = _engine(db_path, tmp_path, _payload(), profil).get_schema()
        assert [t.name for t in schema.tables]


def test_get_schema_filtre_pour_support(db_path, tmp_path):
    schema = _engine(db_path, tmp_path, _payload(), "support").get_schema()
    produits = next(t for t in schema.tables if t.name == "produits")
    noms = [c.name for c in produits.columns]
    assert "prix_achat_ht" not in noms
    assert "marge_pct" not in noms
    assert "prix_vente_ht" in noms


def test_question_metier_executee(db_path, tmp_path):
    resultat = _engine(db_path, tmp_path, _payload()).ask_database("stock de REF-8842 ?")
    assert resultat.status == "ok"
    assert resultat.rows == ((347,),)
    assert resultat.truncated is False
    assert resultat.sql_genere.startswith("SELECT SUM")


def test_tentative_d_ecriture_refusee_sans_appel_llm(db_path, tmp_path):
    llm = FakeLLM(_payload())
    engine = _engine(db_path, tmp_path, _payload(), llm=llm)
    resultat = engine.ask_database("supprime les commandes de test")
    assert resultat.status == "refused"
    assert resultat.code == "FORBIDDEN"
    assert resultat.sql_genere == ""
    assert llm.last_system == ""  # le modèle n'a pas été appelé du tout


def test_tentative_d_ecriture_dupliquee_dans_le_journal_d_alerte(db_path, tmp_path):
    _engine(db_path, tmp_path, _payload()).ask_database("vide la table ventes")
    alertes = (tmp_path / "alerte.jsonl").read_text("utf-8").strip().splitlines()
    assert len(alertes) == 1
    assert json.loads(alertes[0])["code"] == "FORBIDDEN"


def test_sql_d_ecriture_genere_refuse_par_la_validation(db_path, tmp_path):
    # Le modèle ne doit jamais être cru sur parole : ici il retourne du SQL d'écriture
    # tout en déclarant un statut SQL_GENERABLE (conception § 2.1).
    resultat = _engine(db_path, tmp_path, _payload(
        sql="DELETE FROM ventes", tables_referencees=["ventes"],
        colonnes_referencees=["ventes.id"],
    )).ask_database("nettoie les ventes obsoletes")
    assert resultat.status == "refused"
    assert resultat.code == "VALIDATION"
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM ventes").fetchone()[0] == 1


def test_colonne_interdite_declaree_refusee_avant_execution(db_path, tmp_path):
    # Vérification des références déclarées contre le schéma filtré (conception § 2.11).
    resultat = _engine(db_path, tmp_path, _payload(
        sql="SELECT marge_pct FROM produits", tables_referencees=["produits"],
        colonnes_referencees=["produits.marge_pct"],
    ), "support").ask_database("marge de REF-8842 ?")
    assert resultat.status == "refused"
    assert resultat.code == "FORBIDDEN"


def test_colonne_interdite_cachee_dans_le_sql_bloquee_par_l_authorizer(db_path, tmp_path):
    # Déclaration honnête mais SQL incohérent : la couche suivante doit rattraper
    # (conception § 2.11, « ne remplace pas les contrôles en aval »).
    resultat = _engine(db_path, tmp_path, _payload(
        sql="SELECT ref, marge_pct FROM produits", tables_referencees=["produits"],
        colonnes_referencees=["produits.ref"],
    ), "support").ask_database("liste des produits")
    assert resultat.status == "refused"
    assert resultat.code == "FORBIDDEN"


def test_table_hallucinee_detectee(db_path, tmp_path):
    resultat = _engine(db_path, tmp_path, _payload(
        sql="SELECT total FROM facturation", tables_referencees=["facturation"],
        colonnes_referencees=["facturation.total"],
    )).ask_database("total facturé ?")
    assert resultat.status == "refused"
    assert resultat.code == "OUT_OF_SCHEMA"


def test_question_ambigue_demande_une_clarification(db_path, tmp_path):
    resultat = _engine(db_path, tmp_path, _payload(
        status="AMBIGUOUS", sql="", tables_referencees=[], colonnes_referencees=[],
        clarification="Quel critère définit le meilleur client ?",
    )).ask_database("quel est le meilleur client ?")
    assert resultat.status == "clarification"
    assert resultat.code == "AMBIGUOUS"
    assert "critère" in resultat.message
    assert resultat.rows == ()


def test_hors_schema_message_fixe_pas_celui_du_modele(db_path, tmp_path):
    # Le reason du modèle décrit ce qui manque malgré l'instruction contraire
    # (mesuré, spec § 2.11) : il part dans la trace, pas dans la réponse (spec § 4.6).
    engine = _engine(db_path, tmp_path, _payload(
        status="OUT_OF_SCHEMA", sql="", tables_referencees=[], colonnes_referencees=[],
        reason="Il manque un coût d'achat ou un prix de revient.",
    ), "support")
    resultat = engine.ask_database("quelle est la marge sur REF-8842 ?")
    assert resultat.status == "refused"
    assert "coût d'achat" not in resultat.message
    assert any("coût d'achat" in str(e.get("detail", "")) for e in _journal(tmp_path))


def test_chaque_sortie_est_tracee_avec_les_deux_sql(db_path, tmp_path):
    _engine(db_path, tmp_path, _payload()).ask_database("stock de REF-8842 ?")
    entree = _journal(tmp_path)[-1]
    assert entree["tool"] == "ask_database"
    assert entree["statut"] == "ok"
    assert entree["sql_genere"].startswith("SELECT SUM")
    assert "LIMIT" not in entree["sql_genere"]  # généré : tel que produit par le modèle
    assert entree["profil"] == "commercial"


def test_tools_figes_accessibles_et_traces(db_path, tmp_path):
    engine = _engine(db_path, tmp_path, _payload(), "support")
    stock = engine.check_stock("REF-8842")
    assert stock.total_quantity == 347
    commande = engine.order_status("CMD-2026-0042")
    assert commande.found is False
    outils = [e["tool"] for e in _journal(tmp_path)]
    assert outils == ["check_stock", "order_status"]


def test_prompt_de_generation_ne_contient_pas_les_colonnes_interdites(db_path, tmp_path):
    # Première barrière (conception § 3.4) : le modèle ne doit même pas savoir que
    # ces colonnes existent, pour réduire la probabilité qu'il les demande.
    llm = FakeLLM(_payload())
    _engine(db_path, tmp_path, _payload(), "support", llm=llm).ask_database("stock ?")
    assert "marge_pct" not in llm.last_system
    assert "prix_achat_ht" not in llm.last_system
    assert "prix_vente_ht" in llm.last_system  # les colonnes autorisées, elles, y sont
