import json

from retrieval.corpus import IndexedChunk
from retrieval.engine import CurrentVersionSummary, Hit, ListSourcesResponse
from retrieval.engine import SearchDocResult, SearchDocsResponse, SourceSummary
from sql.engine import AskDatabaseResult
from sql.schema import ColumnInfo, SchemaResponse, TableInfo
from sql.tools import CheckStockResult, OrderStatusResult, WarehouseStock


def _chunk(**over) -> IndexedChunk:
    base = dict(
        chunk_id="proc-retour-01-v1.0#0", document_id="proc-retour-01-v1.0",
        content="Contenu.", title="Retour produit", type_doc="procedure_sav",
        collection="procedures_sav", version="1.0", date="2024-08-15",
        source="sav/proc-retour-01-v1.0.html", family_id="proc-retour-01",
        diversification_group="proc-retour", ref_produit=None,
    )
    base.update(over)
    return IndexedChunk(**base)


def test_envelope_ok_isError_false_et_pas_de_meta():
    from mcp_server.envelope import Envelope

    result = Envelope("ok", {"x": 1}, "").to_call_tool_result()
    assert result.isError is False
    assert result.meta is None
    body = json.loads(result.content[0].text)
    assert body == {"status": "ok", "payload": {"x": 1}, "message": ""}


def test_envelope_refuse_isError_true_et_code_dans_meta():
    from mcp_server.envelope import Envelope

    result = Envelope("refused", {}, "non autorisé", "FORBIDDEN").to_call_tool_result()
    assert result.isError is True
    assert result.meta == {"sorabel/error_code": "FORBIDDEN"}


def test_envelope_rejette_un_code_hors_des_4_minimums():
    import pytest

    from mcp_server.envelope import Envelope

    with pytest.raises(ValueError, match="code d'erreur"):
        Envelope("refused", {}, "x", "VALIDATION")


def test_answer_question_hors_corpus():
    from mcp_server.envelope import answer_question_envelope

    env = answer_question_envelope(True, "pertinence insuffisante", "", [])
    assert env.status == "hors_corpus"
    assert env.error_code == "OUT_OF_CORPUS"
    assert env.message == "pertinence insuffisante"


def test_answer_question_ok_avec_reference_de_repli_si_pas_de_ref_produit():
    # E1 : chaque source cite titre + référence + date. Un document sans ref_produit
    # (procedure_sav, notes) utilise son document_id comme référence, pour ne jamais
    # renvoyer une référence vide (vérifié empiriquement sur le corpus SAV, spec_mcp.md).
    from mcp_server.envelope import answer_question_envelope

    hit = Hit(chunk=_chunk(), rerank_score=0.9)
    env = answer_question_envelope(False, None, "La réponse.", [hit])
    assert env.status == "ok"
    assert env.payload["answer"] == "La réponse."
    source = env.payload["sources"][0]
    assert source["titre"] == "Retour produit"
    assert source["reference"] == "proc-retour-01-v1.0"  # repli sur document_id
    assert source["date"] == "2024-08-15"


def test_answer_question_ok_avec_ref_produit_utilise_ref_produit():
    from mcp_server.envelope import answer_question_envelope

    hit = Hit(chunk=_chunk(ref_produit="REF-8842"), rerank_score=0.9)
    env = answer_question_envelope(False, None, "Réponse.", [hit])
    assert env.payload["sources"][0]["reference"] == "REF-8842"


def test_search_docs_envelope_porte_doc_type():
    from mcp_server.envelope import search_docs_envelope

    result = SearchDocResult(
        chunk_id="REF-8842#0", rank=1, title="Fiche REF-8842", ref_produit="REF-8842",
        version="2.1", date="2026-01-10", source="fiches/REF-8842.pdf", content="...",
        rrf_score=None, type_doc="fiche_technique",
    )
    env = search_docs_envelope(SearchDocsResponse(results=[result], query="x", retrieval_count=1))
    assert env.status == "ok"
    hit = env.payload["hits"][0]
    assert hit["metadata"]["reference"] == "REF-8842"
    assert hit["metadata"]["doc_type"] == "fiche_technique"


def test_get_document_envelope_absent():
    from mcp_server.envelope import get_document_envelope

    env = get_document_envelope(None)
    assert env.status == "error"


def test_get_document_envelope_present():
    from mcp_server.envelope import get_document_envelope

    env = get_document_envelope(_chunk())
    assert env.status == "ok"
    assert env.payload["text"] == "Contenu."
    assert env.payload["metadata"]["doc_type"] == "procedure_sav"


def test_list_sources_envelope():
    from mcp_server.envelope import list_sources_envelope

    source = SourceSummary(
        family_id="colis", current_version=CurrentVersionSummary(
            document_id="colis-v2.0", title="Colis abîmé", version="2.0",
            date="2025-01-01", chunk_count=1,
        ), older_versions=[], ref_produit=None, type_doc="procedure_sav",
        collection="procedures_sav",
    )
    env = list_sources_envelope(ListSourcesResponse(sources=[source], total_count=1, filters_applied={}))
    assert env.status == "ok"
    assert env.payload["sources"][0]["doc_type"] == "procedure_sav"
    assert env.payload["total_count"] == 1


def test_get_schema_envelope():
    from mcp_server.envelope import get_schema_envelope

    schema = SchemaResponse(
        tables=(TableInfo(name="produits", description="", columns=(
            ColumnInfo(name="ref", type="TEXT", description="Référence", values=None),
        )),),
        relations=("commandes.client_id -> clients.id",),
    )
    env = get_schema_envelope(schema)
    assert env.status == "ok"
    assert env.payload["tables"][0]["columns"][0]["name"] == "ref"
    assert env.payload["relations"] == ["commandes.client_id -> clients.id"]


def test_ask_database_envelope_ok_ne_porte_pas_le_sql():
    from mcp_server.envelope import ask_database_envelope

    result = AskDatabaseResult(
        status="ok", columns=("n",), rows=((3,),), row_count=1, truncated=False,
        message="", code=None, sql_genere="SELECT ...", sql_execute="SELECT ...",
    )
    env = ask_database_envelope(result)
    assert env.status == "ok"
    assert "sql" not in env.payload
    assert env.payload["rows"] == [[3]]


def test_ask_database_envelope_refuse_porte_le_code_minimum():
    from mcp_server.envelope import ask_database_envelope

    result = AskDatabaseResult(
        status="refused", columns=(), rows=(), row_count=0, truncated=False,
        message="Cette demande n'est pas autorisée.", code="FORBIDDEN",
        sql_genere="", sql_execute="",
    )
    env = ask_database_envelope(result)
    assert env.status == "refused"
    assert env.error_code == "FORBIDDEN"


def test_ask_database_envelope_refuse_code_hors_minimum_devient_none():
    # VALIDATION/TIMEOUT ne font pas partie des 4 codes minimums (conception § 4.2,
    # « point ouvert ») : le code MCP reste absent, le message reste explicite.
    from mcp_server.envelope import ask_database_envelope

    result = AskDatabaseResult(
        status="refused", columns=(), rows=(), row_count=0, truncated=False,
        message="La requête produite n'est pas une lecture valide.", code="VALIDATION",
        sql_genere="", sql_execute="",
    )
    env = ask_database_envelope(result)
    assert env.error_code is None
    assert env.status == "refused"


def test_check_stock_envelope():
    from mcp_server.envelope import check_stock_envelope

    result = CheckStockResult(
        ref="REF-8842", found=True, total_quantity=12,
        by_warehouse=(WarehouseStock(entrepot="LILLE", quantite=12),),
    )
    env = check_stock_envelope(result)
    assert env.status == "ok"
    assert env.payload["total_quantity"] == 12


def test_order_status_envelope_introuvable_reste_ok():
    # « commande introuvable » n'est pas une erreur (conception § 4.2) : le tool a
    # répondu correctement, found=False le dit.
    from mcp_server.envelope import order_status_envelope

    result = OrderStatusResult(
        order_id="CMD-2026-0042", found=False, status=None, date_commande=None,
        montant_ht=None,
    )
    env = order_status_envelope(result)
    assert env.status == "ok"
    assert env.payload["found"] is False
