"""Intégration : dispatch() contre de vrais moteurs, sans protocole stdio.

Le protocole MCP réel (list_tools/call_tool sur stdio) est déjà vérifié par la suite
d'acceptance — ce test-ci vérifie l'assemblage moteurs + enveloppe + journal, plus
vite et sans sous-processus.
"""

import json
from pathlib import Path

import chromadb
import pytest

from gateway.chroma import open_collection
from gateway.settings import get_settings
from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix
from mcp_server.server import dispatch
from retrieval.engine import SearchEngine
from retrieval.tokenize import tokenize
from sql.engine import SqlEngine
from sql.trace import JsonlTraceRecorder

DB_PATH = Path("data/sorabel.db")
pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="data/sorabel.db absente — lancer `make seed`"
)


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * 16
            for token in tokenize(text):
                vec[hash(token) % 16] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


class FixedLLM:
    """Un client factice : les tools testés ici n'ont pas besoin d'un vrai LLM
    (check_stock/order_status sont figés, get_schema n'appelle pas le modèle, et le
    refus d'écriture est détecté avant tout appel — sql/generate.py:looks_like_write)."""

    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                raise AssertionError("le LLM ne doit pas être appelé pour ces tools")


@pytest.fixture(scope="module")
def search_engine():
    client = chromadb.EphemeralClient()
    collection = open_collection(client, "mcp_server_integration_test")
    collection.upsert(
        ids=["REF-8842#0"],
        documents=["Disjoncteur triphasé 32A, REF-8842."],
        embeddings=FakeEmbedder().embed(["Disjoncteur triphasé 32A, REF-8842."]),
        metadatas=[{
            "document_id": "REF-8842", "title": "Disjoncteur REF-8842",
            "type_doc": "fiche_technique", "collection": "fiches_techniques",
            "version": "2.1", "date": "2026-01-10", "source": "fiches/REF-8842.pdf",
            "family_id": "REF-8842", "diversification_group": "REF-8842",
            "ref_produit": "REF-8842",
        }],
    )
    return SearchEngine(collection, FakeEmbedder(), get_settings(), reranker=None)


@pytest.fixture()
def access_rules():
    return YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))


def _sql_engine(profile, access_rules, tmp_path) -> SqlEngine:
    trace = JsonlTraceRecorder(tmp_path / "journal.jsonl", tmp_path / "alertes.jsonl")
    return SqlEngine(profile, access_rules, trace, FixedLLM(), get_settings())


async def _call(name, arguments, *, profile, search_engine, sql_engine, trace):
    return await dispatch(
        name, arguments, profile=profile, search_engine=search_engine,
        sql_engine=sql_engine, llm_client=FixedLLM(), settings=get_settings(), trace=trace,
    )


async def test_check_stock_ok_sans_appel_llm(search_engine, access_rules, tmp_path):
    trace = JsonlTraceRecorder(tmp_path / "journal.jsonl", tmp_path / "alertes.jsonl")
    sql_engine = _sql_engine("commercial", access_rules, tmp_path)
    result = await _call(
        "check_stock", {"ref": "REF-8842"}, profile="commercial",
        search_engine=search_engine, sql_engine=sql_engine, trace=trace,
    )
    body = json.loads(result.content[0].text)
    assert result.isError is False
    assert body["status"] == "ok"
    assert body["payload"]["ref"] == "REF-8842"


async def test_get_schema_filtre_les_colonnes_sensibles_pour_support(
    search_engine, access_rules, tmp_path
):
    trace = JsonlTraceRecorder(tmp_path / "journal.jsonl", tmp_path / "alertes.jsonl")
    sql_engine = _sql_engine("support", access_rules, tmp_path)
    result = await _call(
        "get_schema", {}, profile="support", search_engine=search_engine,
        sql_engine=sql_engine, trace=trace,
    )
    body = json.loads(result.content[0].text)
    assert body["status"] == "ok"  # accessible, seul le contenu est filtré
    colonnes = {
        (t["name"], c["name"]) for t in body["payload"]["tables"] for c in t["columns"]
    }
    assert ("produits", "prix_achat_ht") not in colonnes
    assert ("produits", "marge_pct") not in colonnes
    assert ("ventes", "marge_ht") not in colonnes


async def test_ask_database_refuse_une_tentative_d_ecriture_et_journalise(
    search_engine, access_rules, tmp_path
):
    journal = tmp_path / "journal.jsonl"
    trace = JsonlTraceRecorder(journal, tmp_path / "alertes.jsonl")
    sql_engine = _sql_engine("commercial", access_rules, tmp_path)
    result = await _call(
        "ask_database", {"question": "supprime les commandes de test"},
        profile="commercial", search_engine=search_engine, sql_engine=sql_engine,
        trace=trace,
    )
    body = json.loads(result.content[0].text)
    assert result.isError is True
    assert body["status"] == "refused"
    assert "sql" not in body["payload"]  # spec_mcp.md § 4.1
    entries = [json.loads(x) for x in journal.read_text("utf-8").splitlines()]
    assert any(e["tool"] == "ask_database" and e["statut"] == "refused" for e in entries)


async def test_search_docs_trouve_par_reference_exacte_et_journalise(
    search_engine, access_rules, tmp_path
):
    journal = tmp_path / "journal.jsonl"
    trace = JsonlTraceRecorder(journal, tmp_path / "alertes.jsonl")
    sql_engine = _sql_engine("support", access_rules, tmp_path)
    result = await _call(
        "search_docs", {"query": "REF-8842"}, profile="support",
        search_engine=search_engine, sql_engine=sql_engine, trace=trace,
    )
    body = json.loads(result.content[0].text)
    assert body["payload"]["hits"][0]["metadata"]["reference"] == "REF-8842"
    entries = [json.loads(x) for x in journal.read_text("utf-8").splitlines()]
    assert entries and entries[0]["tool"] == "search_docs" and entries[0]["profil"] == "support"
