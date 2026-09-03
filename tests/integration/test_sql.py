"""Intégration : le moteur SQL contre la vraie base (data/sorabel.db, make seed).

Aucun appel réseau : le client LLM est un double. Ce qui est vérifié ici, c'est
l'accord entre le code et le contenu réel de la base.
"""

import hashlib
import json
from pathlib import Path

import pytest

from gateway.settings import get_settings
from sql.access import SENSITIVE_COLUMNS, StaticAccessRules
from sql.engine import SqlEngine
from sql.guard import open_introspection
from sql.schema import covered_months, read_schema, schema_as_prompt
from sql.trace import NullTraceRecorder

DB_PATH = Path("data/sorabel.db")


class FixedLLM:
    """Retourne un SQL écrit à la main : on teste le moteur, pas le modèle."""

    def __init__(self, sql: str, tables: list[str], columns: list[str]) -> None:
        payload = {
            "status": "SQL_GENERABLE", "sql": sql, "tables_referencees": tables,
            "colonnes_referencees": columns, "clarification": "", "reason": "",
        }

        class _Completions:
            def create(self, **kwargs):
                class _M:
                    content = json.dumps(payload)

                class _C:
                    message = _M()

                class _R:
                    choices = [_C()]

                return _R()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="data/sorabel.db absente — lancer `make seed`"
)


def _engine(profile: str, llm=None) -> SqlEngine:
    return SqlEngine(
        profile=profile,
        access_rules=StaticAccessRules(),
        trace=NullTraceRecorder(),
        llm_client=llm or FixedLLM("SELECT 1", [], []),
        settings=get_settings(),
    )


def test_schema_couvre_les_cinq_tables_et_quatre_relations():
    schema = _engine("commercial").get_schema()
    assert [t.name for t in schema.tables] == [
        "clients", "commandes", "produits", "stocks", "ventes",
    ]
    assert set(schema.relations) == {
        "commandes.client_id -> clients.id",
        "stocks.ref -> produits.ref",
        "ventes.commande_id -> commandes.id",
        "ventes.ref -> produits.ref",
    }


def test_les_trois_colonnes_sensibles_sont_filtrees_pour_support():
    schema = _engine("support").get_schema()
    visibles = {(t.name, c.name) for t in schema.tables for c in t.columns}
    assert not (visibles & SENSITIVE_COLUMNS)


def test_les_colonnes_sensibles_sont_visibles_pour_commercial():
    schema = _engine("commercial").get_schema()
    visibles = {(t.name, c.name) for t in schema.tables for c in t.columns}
    assert SENSITIVE_COLUMNS <= visibles


def test_check_stock_sur_la_vraie_reference():
    resultat = _engine("support").check_stock("REF-8842")
    assert resultat.total_quantity == 774
    assert [(w.entrepot, w.quantite) for w in resultat.by_warehouse] == [
        ("LILLE", 247), ("LYON", 100), ("NANTES", 427),
    ]


def test_commande_absente_du_jeu_d_evaluation():
    resultat = _engine("support").order_status("CMD-2026-0042")
    assert resultat.found is False


def test_avril_sans_annee_resolu_sur_2026():
    # 27 commandes en avril 2026, 0 en avril 2025 ; 31 en octobre 2025, 0 en octobre
    # 2026 (vérifié). Une devinette « année courante » se tromperait sur octobre.
    connection = open_introspection(get_settings().sqlite_path)
    mois = covered_months(connection)
    assert mois["avril"] == "2026"
    assert mois["octobre"] == "2025"
    schema = read_schema(connection, StaticAccessRules(), "commercial")
    prompt = schema_as_prompt(schema, mois)
    assert "avril -> 2026" in prompt
    assert "octobre -> 2025" in prompt


def test_question_metier_reelle_executee():
    llm = FixedLLM(
        "SELECT COUNT(*) FROM commandes WHERE date_commande >= '2026-04-01' "
        "AND date_commande < '2026-05-01'",
        ["commandes"], ["commandes.date_commande"],
    )
    resultat = _engine("commercial", llm).ask_database("combien de commandes en avril ?")
    assert resultat.status == "ok"
    assert resultat.rows == ((27,),)


def test_troncature_signalee_sur_la_vraie_table_ventes():
    llm = FixedLLM("SELECT id FROM ventes", ["ventes"], ["ventes.id"])
    resultat = _engine("commercial", llm).ask_database("liste des ventes")
    assert resultat.status == "ok"
    assert resultat.row_count == 100  # 993 lignes réelles
    assert resultat.truncated is True


def test_colonne_sensible_refusee_pour_support_sur_la_vraie_base():
    llm = FixedLLM(
        "SELECT ref, marge_pct FROM produits", ["produits"], ["produits.ref"],
    )
    resultat = _engine("support", llm).ask_database("liste des produits")
    assert resultat.status == "refused"
    assert resultat.code == "FORBIDDEN"


def test_sqlite_master_refuse_pour_les_deux_profils():
    llm = FixedLLM("SELECT name FROM sqlite_master", [], [])
    for profil in ("support", "commercial"):
        resultat = _engine(profil, llm).ask_database("liste des tables")
        assert resultat.status == "refused"


def test_la_base_n_est_pas_modifiee_par_la_suite():
    avant = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
    llm = FixedLLM("DELETE FROM ventes", ["ventes"], ["ventes.id"])
    assert _engine("commercial", llm).ask_database("nettoie").status == "refused"
    assert hashlib.sha256(DB_PATH.read_bytes()).hexdigest() == avant
