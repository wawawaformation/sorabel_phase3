"""Acceptance — serveur MCP, matrice d'accès et journal (exigences DSI E4, E5)."""

from __future__ import annotations

from tests.conftest import gateway_session, read_journal


async def test_get_schema_filtre_les_colonnes_sensibles_pour_support():
    # E4/E5 : get_schema est accessible aux deux profils (aucun tool n'est interdit
    # dans son intégralité, spec_mcp.md § 2/§ 4.3) — mais son contenu est filtré :
    # support ne voit jamais les 3 colonnes sensibles.
    async with gateway_session("support") as call:
        support_schema = await call("get_schema", {})
    async with gateway_session("commercial") as call:
        commercial_schema = await call("get_schema", {})

    assert support_schema["status"] == "ok"
    assert commercial_schema["status"] == "ok"

    def colonnes(schema: dict) -> set[tuple[str, str]]:
        return {
            (t["name"], c["name"])
            for t in schema["payload"]["tables"]
            for c in t["columns"]
        }

    support_colonnes = colonnes(support_schema)
    assert ("produits", "prix_achat_ht") not in support_colonnes
    assert ("produits", "marge_pct") not in support_colonnes
    assert ("ventes", "marge_ht") not in support_colonnes
    assert ("produits", "prix_achat_ht") in colonnes(commercial_schema)


async def test_refus_donnee_message_clair_et_journalise(journal_path):
    # E4 + E5 : un refus au niveau donnée (ici, colonne interdite via ask_database —
    # déjà le cas déterministe de test_sql.py::test_profil_support_jamais_de_marge)
    # est explicite et journalisé, même s'il n'existe aucun refus de tool entier.
    async with gateway_session("support", journal_path) as call:
        result = await call("ask_database", {"question": "quelle est la marge sur la REF-8842 ?"})
    assert result["status"] == "refused"
    assert result["message"].strip()

    entries = read_journal(journal_path)
    assert any(
        e["profil"] == "support" and e["tool"] == "ask_database" and e["statut"] == "refused"
        for e in entries
    )


async def test_briques_du_rag_utilisables_separement():
    # E4 : un client qui veut chercher sans générer enchaîne search_docs puis
    # get_document — les briques fonctionnent séparément.
    async with gateway_session("commercial") as call:
        search = await call(
            "search_docs", {"query": "retour d'un produit défectueux sous garantie"}
        )
        assert search["status"] == "ok"
        assert search["payload"]["hits"]

        doc_id = search["payload"]["hits"][0]["doc_id"]
        document = await call("get_document", {"doc_id": doc_id})
    assert document["status"] == "ok"
    assert document["payload"]["text"].strip()
    assert document["payload"]["metadata"]


async def test_journal_exhaustif_autorises_et_refuses(journal_path):
    # E5 : sur une session de démonstration, tous les appels — autorisés comme
    # refusés — figurent au journal.
    calls = [
        ("answer_question", {"question": "délai d'un échange standard ?"}),
        ("check_stock", {"ref": "REF-8842"}),
        # Refus au niveau donnée (colonne interdite) : aucun tool n'est refusé dans
        # son intégralité dans ce MVP (spec_mcp.md § 4.3).
        ("ask_database", {"question": "quelle est la marge sur la REF-8842 ?"}),
    ]
    async with gateway_session("support", journal_path) as call:
        for tool, arguments in calls:
            await call(tool, arguments)

    entries = read_journal(journal_path)
    assert len(entries) == len(calls)
    assert [e["tool"] for e in entries] == [tool for tool, _ in calls]
    statuses = {e["statut"] for e in entries}
    assert "refused" in statuses
    assert statuses - {"refused"}, "le journal doit aussi tracer les appels autorisés"
