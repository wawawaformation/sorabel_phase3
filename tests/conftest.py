"""Outils de la suite d'acceptance : appels boîte noire au serveur MCP (stdio).

La suite joue le rôle d'un client interne : elle lance le serveur selon le
contrat d'intégration de docs/cadrage_dsi.md (commande, profil et journal via
variables d'environnement, enveloppe de réponse JSON) et n'importe rien de son
implémentation.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "eval"
DB_PATH = REPO_ROOT / "data" / "sorabel.db"
SERVER_MODULE = "mcp_server.server"
CALL_TIMEOUT = float(os.environ.get("GATEWAY_TEST_TIMEOUT", "30"))

#: Catalogue MCP (docs/spec_mcp.md § 3.1).
ALL_TOOLS = (
    "answer_question", "search_docs", "get_document", "list_sources",
    "ask_database", "get_schema", "check_stock", "order_status",
)
# Aucun tool n'est interdit dans son intégralité à un profil (spec_mcp.md § 2, point
# 1/4 : contrairement à la matrice de docs/cadrage_dsi.md, écartée — voir
# CHANGELOG.md, 2026-09-01). La seule restriction réelle du système porte sur 3
# colonnes SQL (sql/access.py:SENSITIVE_COLUMNS), pas sur l'accès à un tool.
TOOLS_BY_PROFILE = {"support": set(ALL_TOOLS), "commercial": set(ALL_TOOLS)}


def load_jsonl(name: str) -> list[dict]:
    text = (EVAL_DIR / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def db() -> sqlite3.Connection:
    """Connexion directe (lecture seule) à la base, pour calculer les attendus."""
    if not DB_PATH.exists():
        pytest.fail("data/sorabel.db absente — lancer `make seed` d'abord")
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


@asynccontextmanager
async def gateway_session(profile: str, journal_path: Path | None = None):
    """Session client MCP vers un serveur lancé au profil donné.

    Fournit ``call(tool, arguments) -> dict`` qui décode l'enveloppe JSON
    ``{status, payload, message}`` du contrat d'intégration.
    """
    if importlib.util.find_spec(SERVER_MODULE) is None:
        pytest.fail(f"module `{SERVER_MODULE}` introuvable — le serveur de la gateway "
                    "n'est pas encore construit")

    env = {**os.environ, "SORABEL_PROFILE": profile}
    if journal_path is not None:
        env["GATEWAY_JOURNAL"] = str(journal_path)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", SERVER_MODULE],
        env=env,
        cwd=str(REPO_ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            try:
                await asyncio.wait_for(session.initialize(), CALL_TIMEOUT)
            except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                pytest.fail(f"serveur MCP injoignable via `python -m {SERVER_MODULE}` : "
                            f"{type(exc).__name__}: {exc}")

            async def call(tool: str, arguments: dict) -> dict:
                result = await asyncio.wait_for(
                    session.call_tool(tool, arguments), CALL_TIMEOUT
                )
                texts = [c.text for c in result.content if getattr(c, "text", None)]
                assert texts, f"réponse vide du tool {tool}"
                envelope = json.loads(texts[0])
                assert "status" in envelope, f"enveloppe sans champ status : {envelope}"
                envelope.setdefault("payload", {})
                envelope.setdefault("message", "")
                return envelope

            yield call


async def call_tool(
    profile: str, tool: str, arguments: dict, journal_path: Path | None = None
) -> dict:
    """Un appel isolé : session dédiée, un tool, une enveloppe décodée."""
    async with gateway_session(profile, journal_path) as call:
        return await call(tool, arguments)


def read_journal(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return [json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture()
def journal_path(tmp_path: Path) -> Path:
    return tmp_path / "journal.jsonl"


@pytest.fixture()
def questions_rag() -> list[dict]:
    return load_jsonl("questions_rag.jsonl")


@pytest.fixture()
def questions_sql() -> list[dict]:
    return load_jsonl("questions_sql.jsonl")
