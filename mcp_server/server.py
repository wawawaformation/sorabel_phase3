"""Point d'entrée du serveur MCP — assemble matrice, identité, moteurs, catalogue,
journal et enveloppe pour exposer les 8 tools déjà conçus (chantiers 1 et 2).

``python -m mcp_server.server`` : transport stdio, un process par profil (résolu une
seule fois au démarrage, jamais un paramètre de tool — E4). ``dispatch()`` est la
logique pure (nom de tool + arguments + moteurs -> CallToolResult), testée sans
protocole MCP réel dans tests/integration/test_mcp_server.py ; ``main()`` la branche
au SDK MCP et au transport stdio.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import mcp.types as types
from langfuse import Langfuse
from langfuse.openai import OpenAI
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from gateway.chroma import chroma_client, open_collection
from gateway.embedder import AzureEmbedder
from gateway.settings import Settings, get_settings
from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix
from mcp_server.catalogue import SERVER_INSTRUCTIONS, build_tools
from mcp_server.envelope import (
    Envelope,
    answer_question_envelope,
    ask_database_envelope,
    check_stock_envelope,
    get_document_envelope,
    get_schema_envelope,
    list_sources_envelope,
    order_status_envelope,
    search_docs_envelope,
)
from mcp_server.identity import EnvVarIdentityResolver
from retrieval.answer import compose_answer
from retrieval.engine import SearchEngine
from retrieval.reranker import AzureCohereReranker
from sql.engine import SqlEngine
from sql.trace import JsonlTraceRecorder, TraceRecorder

#: Journal unique (conception/commun/journal_mcp.md), surchargeable par le contrat
#: d'intégration fourni (tests/conftest.py : GATEWAY_JOURNAL).
DEFAULT_JOURNAL_PATH = Path("logs/mcp_audit.jsonl")

#: Tools RAG sans journalisation propre côté moteur (contrairement aux 4 tools SQL,
#: qui s'auto-journalisent déjà dans sql/engine.py._record — spec_mcp.md § 4.5).
_RAG_TOOLS = frozenset({"answer_question", "search_docs", "list_sources", "get_document"})


def build_search_engine(settings: Settings) -> SearchEngine:
    collection = open_collection(chroma_client(settings), settings.chroma_collection)
    reranker = AzureCohereReranker(settings) if settings.rerank_enabled else None
    return SearchEngine(collection, AzureEmbedder(settings), settings, reranker=reranker)


async def dispatch(
    name: str,
    arguments: dict,
    *,
    profile: str,
    search_engine: SearchEngine,
    sql_engine: SqlEngine,
    llm_client: Any,
    settings: Settings,
    trace: TraceRecorder,
) -> types.CallToolResult:
    """Exécute un tool déjà résolu (profil, moteurs) et journalise les tools RAG.

    Les 4 tools SQL journalisent déjà eux-mêmes (``SqlEngine._record``) — ne pas
    dupliquer l'écriture ici, sous peine de deux entrées par appel SQL.
    """
    envelope = await _run(
        name, arguments, search_engine=search_engine, sql_engine=sql_engine,
        llm_client=llm_client, settings=settings,
    )
    if name in _RAG_TOOLS:
        trace.record({
            "profil": profile,
            "tool": name,
            "question": arguments.get("question") or arguments.get("query"),
            "statut": envelope.status,
            "code": envelope.error_code,
            "detail": envelope.message,
        })
    return envelope.to_call_tool_result()


async def _run(
    name: str, arguments: dict, *, search_engine: SearchEngine, sql_engine: SqlEngine,
    llm_client: Any, settings: Settings,
) -> Envelope:
    if name == "answer_question":
        outcome = search_engine.search(arguments["question"], arguments.get("top_k"))
        answer = "" if outcome.is_refusal else compose_answer(
            llm_client, settings.azure_model_text_generation, arguments["question"], outcome.hits
        )
        return answer_question_envelope(outcome.is_refusal, outcome.reason, answer, outcome.hits)
    if name == "search_docs":
        docs = search_engine.search_docs(
            arguments["query"], arguments.get("top_k", 5), arguments.get("include_score", False)
        )
        return search_docs_envelope(docs)
    if name == "get_document":
        return get_document_envelope(search_engine.get_document(arguments["document_id"]))
    if name == "list_sources":
        sources = search_engine.list_sources(
            arguments.get("collection"), arguments.get("type_doc"),
            arguments.get("ref_produit"), arguments.get("include_versions", False),
        )
        return list_sources_envelope(sources)
    if name == "get_schema":
        return get_schema_envelope(sql_engine.get_schema())
    if name == "ask_database":
        return ask_database_envelope(sql_engine.ask_database(arguments["question"]))
    if name == "check_stock":
        return check_stock_envelope(sql_engine.check_stock(arguments["ref"]))
    if name == "order_status":
        return order_status_envelope(sql_engine.order_status(arguments["order_id"]))
    raise ValueError(f"tool inconnu : {name!r}")  # jamais atteint : le catalogue borne les noms


async def main() -> None:
    settings = get_settings()
    profile = EnvVarIdentityResolver().resolve()
    access_rules = YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))
    journal_path = Path(os.environ.get("GATEWAY_JOURNAL", str(DEFAULT_JOURNAL_PATH)))
    trace = JsonlTraceRecorder(journal_path, settings.sql_alert_log)

    Langfuse(
        public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_base_url,
    )
    llm_client = OpenAI(base_url=settings.azure_ai_endpoint, api_key=settings.azure_ai_api_key)

    search_engine = build_search_engine(settings)
    sql_engine = SqlEngine(profile, access_rules, trace, llm_client, settings)

    server = Server("sorabel-data-gateway", instructions=SERVER_INSTRUCTIONS)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return build_tools(access_rules)

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> types.CallToolResult:
        return await dispatch(
            name, arguments, profile=profile, search_engine=search_engine,
            sql_engine=sql_engine, llm_client=llm_client, settings=settings, trace=trace,
        )

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
