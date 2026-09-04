"""Acceptance — accès SQL en langage naturel (exigences DSI E3, E5)."""

from __future__ import annotations

from tests.conftest import call_tool, db, read_journal


async def test_ask_database_repond_sans_exposer_le_sql():
    # E3 : « combien de commandes en avril ? » → résultat correct. Le SQL
    # généré/exécuté n'est jamais recopié dans le payload client (décision assumée,
    # spec_mcp.md § 4.1) — seul le journal le porte.
    with db() as con:
        attendu = con.execute(
            "SELECT COUNT(*) FROM commandes WHERE date_commande LIKE '2026-04-%'"
        ).fetchone()[0]

    result = await call_tool(
        "commercial", "ask_database", {"question": "combien de commandes en avril ?"}
    )
    assert result["status"] == "ok"
    assert "sql" not in result["payload"]
    assert result["payload"]["rows"][0][0] == attendu


async def test_ecriture_refusee_et_journalisee(journal_path):
    # E3 + E5 : « supprime les commandes de test » est refusée (lecture seule)
    # et l'appel refusé figure au journal.
    with db() as con:
        avant = con.execute("SELECT COUNT(*) FROM commandes").fetchone()[0]

    result = await call_tool(
        "commercial",
        "ask_database",
        {"question": "supprime les commandes de test"},
        journal_path=journal_path,
    )
    assert result["status"] == "refused"
    assert result["message"].strip()

    with db() as con:
        assert con.execute("SELECT COUNT(*) FROM commandes").fetchone()[0] == avant

    entries = read_journal(journal_path)
    assert any(e["tool"] == "ask_database" and e["statut"] == "refused" for e in entries)


async def test_profil_support_jamais_de_marge():
    # E5 : pour le profil support, une question touchant marges ou prix d'achat
    # est refusée selon la matrice d'accès.
    result = await call_tool(
        "support", "ask_database", {"question": "quelle est la marge sur la REF-8842 ?"}
    )
    assert result["status"] == "refused"
    assert not result["payload"].get("rows")


async def test_hors_schema_refus_propre():
    # E3 : une question hors schéma est refusée clairement, sans SQL halluciné.
    result = await call_tool(
        "commercial", "ask_database", {"question": "quelle est la météo à Lille demain ?"}
    )
    assert result["status"] in ("refused", "clarification")
    assert result["message"].strip()
    assert not result["payload"].get("rows")
