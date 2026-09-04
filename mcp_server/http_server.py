"""Second point d'entrée du serveur MCP : transport HTTP, identité résolue par
requête via un JWT Keycloak — pas une variable d'environnement lue une fois au
démarrage (``mcp_server/server.py``, inchangé, reste le mode stdio de la suite
d'acceptance).

Réutilise tel quel le catalogue, l'enveloppe et ``dispatch()`` du mode stdio — seule
la construction des moteurs (deux ``SqlEngine``, un par profil, plutôt qu'un choisi au
lancement du process) et la résolution du profil (par requête, via
``get_access_token()``, plutôt qu'une fois via ``SORABEL_PROFILE``) diffèrent.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import jwt
import mcp.types as types
import uvicorn
from chromadb.api.models.Collection import Collection
from langfuse import Langfuse
from langfuse.openai import OpenAI
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware, get_access_token
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.provider import TokenVerifier
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Route

from gateway.embedder import AzureEmbedder, Embedder
from gateway.settings import Settings, get_settings
from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix
from mcp_server.catalogue import SERVER_INSTRUCTIONS, build_tools
from mcp_server.envelope import Envelope
from mcp_server.keycloak_auth import KeycloakTokenVerifier, resolve_profile
from mcp_server.server import dispatch
from retrieval.engine import SearchEngine
from retrieval.reranker import AzureCohereReranker
from sql.engine import SqlEngine
from sql.trace import JsonlTraceRecorder, NullTraceRecorder, TraceRecorder


def build_app(
    *,
    settings: Settings,
    access_rules: YamlAccessRules,
    token_verifier: TokenVerifier,
    search_engine_collection: Collection,
    search_engine_embedder: Embedder,
    llm_client: Any,
    trace: TraceRecorder | None = None,
) -> Starlette:
    """Assemble le serveur MCP low-level (mêmes tools que le stdio) derrière le
    transport HTTP + l'authentification Keycloak. Séparé de ``main()`` pour être
    testable sans réseau ni process uvicorn (tests/integration/test_http_server.py)."""
    trace = trace or NullTraceRecorder()
    reranker = AzureCohereReranker(settings) if settings.rerank_enabled else None
    search_engine = SearchEngine(
        search_engine_collection, search_engine_embedder, settings, reranker=reranker
    )
    sql_engines = {
        profile: SqlEngine(profile, access_rules, trace, llm_client, settings)
        for profile in ("commercial", "support")
    }

    server = Server("sorabel-data-gateway", instructions=SERVER_INSTRUCTIONS)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return build_tools(access_rules)

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> types.CallToolResult:
        access_token = get_access_token()
        profile = resolve_profile(access_token.claims or {}) if access_token else None
        if profile is None:
            return Envelope(
                "refused", {}, "Profil non résolu pour cette identité.", "FORBIDDEN"
            ).to_call_tool_result()
        return await dispatch(
            name, arguments, profile=profile, search_engine=search_engine,
            sql_engine=sql_engines[profile], llm_client=llm_client, settings=settings,
            trace=trace,
        )

    session_manager = StreamableHTTPSessionManager(app=server, stateless=True)

    async def streamable_http_app(scope, receive, send) -> None:
        await session_manager.handle_request(scope, receive, send)

    app = Starlette(
        routes=[Route("/mcp", endpoint=RequireAuthMiddleware(streamable_http_app, []))],
        middleware=[
            Middleware(AuthenticationMiddleware, backend=BearerAuthBackend(token_verifier)),
            Middleware(AuthContextMiddleware),
        ],
        lifespan=lambda _: session_manager.run(),
    )
    # Exposé pour les tests, qui pilotent le cycle de vie du session manager
    # eux-mêmes (``async with app.state.session_manager.run(): ...``) : sans un vrai
    # serveur ASGI (uvicorn), le ``lifespan=`` de Starlette n'est jamais déclenché —
    # vérifié empiriquement. En production (``main()``, via ``uvicorn.run``),
    # ``app.state`` n'est jamais consulté, seul ``lifespan=`` compte.
    app.state.session_manager = session_manager
    return app


def main() -> None:
    settings = get_settings()
    access_rules = YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))
    journal_path = Path(os.environ.get("GATEWAY_JOURNAL", "logs/mcp_audit.jsonl"))
    trace = JsonlTraceRecorder(journal_path, settings.sql_alert_log)

    Langfuse(
        public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_base_url,
    )
    llm_client = OpenAI(base_url=settings.azure_ai_endpoint, api_key=settings.azure_ai_api_key)

    jwks_url = f"{settings.keycloak_issuer}/protocol/openid-connect/certs"
    token_verifier = KeycloakTokenVerifier(
        jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300),
        settings.keycloak_issuer, settings.keycloak_audience,
    )

    from gateway.chroma import chroma_client, open_collection

    collection = open_collection(chroma_client(settings), settings.chroma_collection)
    app = build_app(
        settings=settings, access_rules=access_rules, token_verifier=token_verifier,
        search_engine_collection=collection, search_engine_embedder=AzureEmbedder(settings),
        llm_client=llm_client, trace=trace,
    )
    uvicorn.run(app, host=settings.http_host, port=settings.http_port)


if __name__ == "__main__":
    main()
