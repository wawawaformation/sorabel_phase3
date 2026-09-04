"""Enveloppe commune : état interne des moteurs -> contrat MCP + JSON du scaffold fourni.

Double enveloppe assumée (spec_mcp.md § 4.4) : ``CallToolResult.isError`` +
``_meta["sorabel/error_code"]`` portent le contrat MCP natif de la conception ; le
premier bloc ``content`` est un texte JSON ``{status, payload, message}``, vocabulaire
imposé par ``tests/conftest.py`` (``ok | refused | clarification | hors_corpus |
error``). Les deux sont toujours synchrones : ``isError`` est vrai si et seulement si
``status != "ok"``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import mcp.types as types

from retrieval.corpus import IndexedChunk
from retrieval.engine import Hit, ListSourcesResponse, SearchDocsResponse
from sql.engine import AskDatabaseResult
from sql.schema import SchemaResponse
from sql.tools import CheckStockResult, OrderStatusResult

#: Codes minimums de la conception (questions_reponses_mcp.md § 4.2). Un refus de
#: validation structurelle (ex. VALIDATION, TIMEOUT côté SQL) n'y figure pas — le code
#: MCP reste absent (None), le message métier reste explicite (« point ouvert » assumé).
ERROR_CODES = frozenset({"FORBIDDEN", "OUT_OF_CORPUS", "OUT_OF_SCHEMA", "AMBIGUOUS"})


@dataclass(frozen=True)
class Envelope:
    status: str
    payload: dict
    message: str
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.error_code is not None and self.error_code not in ERROR_CODES:
            raise ValueError(f"code d'erreur non reconnu : {self.error_code!r}")

    def to_call_tool_result(self) -> types.CallToolResult:
        body = {"status": self.status, "payload": self.payload, "message": self.message}
        # "_meta" est l'alias pydantic du champ "meta" (alias_priority=2, model_config
        # extra="allow") : le mot-clé "meta=" est silencieusement traité comme un champ
        # supplémentaire et n'atteint jamais le champ réel — vérifié empiriquement.
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(body, ensure_ascii=False))],
            isError=self.status != "ok",
            **{"_meta": {"sorabel/error_code": self.error_code}} if self.error_code else {},
        )


def _citation(chunk: IndexedChunk) -> dict:
    """Titre + référence + date (E1). ``ref_produit`` est absent pour les collections
    sav/ et notes/ (spec § 2.5 du chantier RAG) : le document_id sert alors de
    référence, pour ne jamais renvoyer une citation sans référence."""
    return {
        "titre": chunk.title,
        "reference": chunk.ref_produit or chunk.document_id,
        "date": chunk.date,
    }


def answer_question_envelope(
    is_refusal: bool, reason: str | None, answer: str, hits: list[Hit]
) -> Envelope:
    if is_refusal:
        return Envelope("hors_corpus", {}, reason or "Aucune source pertinente.", "OUT_OF_CORPUS")
    sources = [_citation(hit.chunk) for hit in hits]
    return Envelope("ok", {"answer": answer, "sources": sources}, "")


def search_docs_envelope(response: SearchDocsResponse) -> Envelope:
    hits = [
        {
            "doc_id": r.chunk_id,
            "score": r.rrf_score,
            "text": r.content,
            "metadata": {
                "reference": r.ref_produit or "",
                "doc_type": r.type_doc,
                "version": r.version,
                "date": r.date,
            },
        }
        for r in response.results
    ]
    return Envelope("ok", {"hits": hits}, "")


def get_document_envelope(chunk: IndexedChunk | None) -> Envelope:
    if chunk is None:
        return Envelope("error", {}, "Document introuvable.")
    metadata = {
        "doc_id": chunk.document_id, "titre": chunk.title,
        "reference": chunk.ref_produit or "", "version": chunk.version, "date": chunk.date,
        "doc_type": chunk.type_doc, "collection": chunk.collection, "source": chunk.source,
    }
    return Envelope("ok", {"text": chunk.content, "metadata": metadata}, "")


def list_sources_envelope(response: ListSourcesResponse) -> Envelope:
    sources = [
        {
            "doc_id": s.current_version.document_id, "titre": s.current_version.title,
            "reference": s.ref_produit or "", "version": s.current_version.version,
            "date": s.current_version.date, "doc_type": s.type_doc,
        }
        for s in response.sources
    ]
    return Envelope("ok", {"sources": sources, "total_count": response.total_count}, "")


def get_schema_envelope(schema: SchemaResponse) -> Envelope:
    tables = [
        {
            "name": t.name, "description": t.description,
            "columns": [
                {"name": c.name, "type": c.type, "description": c.description,
                 "values": list(c.values) if c.values else []}
                for c in t.columns
            ],
        }
        for t in schema.tables
    ]
    return Envelope("ok", {"tables": tables, "relations": list(schema.relations)}, "")


def ask_database_envelope(result: AskDatabaseResult) -> Envelope:
    # Décision de cette session (spec_mcp.md § 4.1) : le SQL généré/exécuté n'est
    # jamais recopié dans le payload client, malgré le brief et le test fourni — seul
    # le journal le porte (déjà fait par sql/engine.py._record).
    if result.status == "ok":
        payload = {
            "columns": list(result.columns), "rows": [list(row) for row in result.rows],
            "row_count": result.row_count, "truncated": result.truncated,
        }
        return Envelope("ok", payload, "")
    code = result.code if result.code in ERROR_CODES else None
    return Envelope(result.status, {"rows": []}, result.message, code)


def check_stock_envelope(result: CheckStockResult) -> Envelope:
    payload = {
        "ref": result.ref, "found": result.found, "total_quantity": result.total_quantity,
        "by_warehouse": [{"entrepot": w.entrepot, "quantite": w.quantite}
                          for w in result.by_warehouse],
    }
    return Envelope("ok", payload, "")


def order_status_envelope(result: OrderStatusResult) -> Envelope:
    payload = {
        "order_id": result.order_id, "found": result.found, "status": result.status,
        "date_commande": result.date_commande, "montant_ht": result.montant_ht,
    }
    return Envelope("ok", payload, "")
