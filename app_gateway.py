"""Démo de la gateway complète — interface Streamlit passant par le vrai serveur MCP.

Contrairement à app.py/app_sql.py (qui instancient SearchEngine/SqlEngine
directement), cette appli est un vrai client MCP (mêmes primitives que
scripts/mcp_client.py) : elle relance une session stdio vers
`python -m mcp_server.server` à chaque appel, exactement comme le ferait un host MCP
réel. Streamlit est synchrone : chaque appel ouvre et referme sa propre session
(asyncio.run), plus simple à raisonner qu'une session persistante partagée entre les
reruns du script (spec_mcp.md § 6).

Usage : ``make ui-gateway`` ou ``uv run streamlit run app_gateway.py``. Nécessite
``make seed``, ``make up`` et un ``.env`` valide (le serveur MCP en sous-processus lit
sa propre configuration).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import streamlit as st
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from sql.access import PROFILES

TOOLS = (
    "answer_question", "search_docs", "get_document", "list_sources",
    "ask_database", "get_schema", "check_stock", "order_status",
)

#: Un exemple d'arguments par tool, pour préremplir le formulaire de démo.
EXAMPLE_ARGS = {
    "answer_question": {"question": "quelle est la procédure de retour sous garantie ?"},
    "search_docs": {"query": "REF-8842"},
    "get_document": {"doc_id": ""},
    "list_sources": {"ref_produit": "REF-8842"},
    "ask_database": {"question": "combien de commandes en avril ?"},
    "get_schema": {},
    "check_stock": {"ref": "REF-8842"},
    "order_status": {"order_id": "CMD-2026-0042"},
}


async def call_gateway(profile: str, tool: str, arguments: dict) -> dict:
    """Ouvre une session MCP stdio dédiée, appelle un tool, referme la session."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        env={**os.environ, "SORABEL_PROFILE": profile},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
            text = next(c.text for c in result.content if getattr(c, "text", None))
            return json.loads(text)


st.set_page_config(page_title="Sorabel Data Gateway — démo", page_icon="🗝️")
st.title("🗝️ Sorabel Data Gateway — démo bout en bout")
st.caption(
    "Vrai client MCP (stdio) vers `python -m mcp_server.server` — le profil change le "
    "process serveur lancé, jamais un paramètre de tool (E4)."
)

with st.sidebar:
    st.header("Profil")
    profile = st.selectbox("Profil de connexion", sorted(PROFILES))
    tool = st.selectbox("Tool", TOOLS)

st.subheader(f"Appel `{tool}` en profil `{profile}`")
args_text = st.text_area(
    "Arguments (JSON)", value=json.dumps(EXAMPLE_ARGS[tool], ensure_ascii=False, indent=2),
    height=120,
)

if st.button("Appeler"):
    try:
        arguments = json.loads(args_text)
    except json.JSONDecodeError as error:
        st.error(f"JSON invalide : {error}")
    else:
        with st.spinner("Appel du serveur MCP…"):
            envelope = asyncio.run(call_gateway(profile, tool, arguments))
        {"ok": st.success, "refused": st.error, "clarification": st.warning,
         "hors_corpus": st.warning, "error": st.error}[envelope["status"]](
            f"**{envelope['status']}**"
        )
        if envelope["message"]:
            st.write(envelope["message"])
        st.json(envelope["payload"])
