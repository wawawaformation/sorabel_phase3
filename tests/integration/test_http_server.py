"""Intégration : l'app Starlette complète (auth + dispatch), en mémoire (ASGITransport).

Pas de Keycloak réel : un TokenVerifier factice signe/valide avec une clé RSA de test
(même patron que tests/unit/test_keycloak_auth.py). Le protocole MCP réel (HTTP +
JSON-RPC) est exercé via mcp.client.streamable_http, pas un client HTTP brut — c'est le
même contrat que verra un vrai host MCP.
"""

import time
from pathlib import Path

import chromadb
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.auth.provider import AccessToken

from gateway.chroma import open_collection
from gateway.settings import get_settings
from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix
from mcp_server.http_server import build_app
from retrieval.tokenize import tokenize

DB_PATH = Path("data/sorabel.db")
pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="data/sorabel.db absente — lancer `make seed`"
)

ISSUER = "http://testserver/realms/sorabel"
AUDIENCE = "sorabel-gateway"


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
    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                raise AssertionError("le LLM ne doit pas être appelé pour ces tools")


_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class DirectTokenVerifier:
    """Valide directement avec la clé publique de test — pas de JWKS HTTP à simuler,
    ce n'est pas ce que ce test vérifie (déjà couvert par test_keycloak_auth.py)."""

    async def verify_token(self, token: str):
        try:
            claims = jwt.decode(
                token, _PRIVATE_KEY.public_key(), algorithms=["RS256"], issuer=ISSUER,
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return None
        if claims.get("azp") != AUDIENCE:
            return None
        return AccessToken(token=token, client_id=AUDIENCE, scopes=[],
                            expires_at=claims["exp"], subject=claims["sub"], claims=claims)


def _token(roles: list[str], exp_delta: int = 300) -> str:
    claims = {"sub": "u1", "iss": ISSUER, "azp": AUDIENCE,
              "exp": int(time.time()) + exp_delta, "realm_access": {"roles": roles}}
    return jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256")


@pytest.fixture()
def app():
    # scope="function", pas "module" : StreamableHTTPSessionManager.run() ne peut être
    # entré qu'une seule fois par instance (vérifié empiriquement) — chaque test a
    # besoin de sa propre app/session_manager.
    settings = get_settings()
    collection = open_collection(chromadb.EphemeralClient(), "http_server_integration_test")
    access_rules = YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))
    return build_app(
        settings=settings, access_rules=access_rules, token_verifier=DirectTokenVerifier(),
        search_engine_collection=collection, search_engine_embedder=FakeEmbedder(),
        llm_client=FixedLLM(),
    )


def _client_factory(app):
    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
            headers=headers, timeout=timeout or 30,
        )
    return factory


async def _call(app, token: str, tool: str, arguments: dict):
    async with app.state.session_manager.run():
        async with streamablehttp_client(
            "http://testserver/mcp", headers={"Authorization": f"Bearer {token}"},
            httpx_client_factory=_client_factory(app),
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool, arguments)


async def test_appel_authentifie_commercial_obtient_le_stock(app):
    result = await _call(app, _token(["commercial"]), "check_stock", {"ref": "REF-8842"})
    assert result.isError is False


async def test_appel_authentifie_support_colonnes_filtrees(app):
    result = await _call(app, _token(["support"]), "get_schema", {})
    assert result.isError is False
    assert "prix_achat_ht" not in result.content[0].text


async def test_sans_token_refuse_401(app):
    with pytest.raises(Exception):  # httpx.HTTPStatusError : 401, avant notre code
        await _call(app, "", "get_schema", {})


async def test_role_absent_refus_controle_pas_401(app):
    # Jeton valide, mais aucun rôle commercial/support : refus FORBIDDEN de notre
    # enveloppe, pas une erreur HTTP brute (distinction authn/authz, spec § 3.2).
    result = await _call(app, _token(["offline_access"]), "get_schema", {})
    assert result.isError is True
