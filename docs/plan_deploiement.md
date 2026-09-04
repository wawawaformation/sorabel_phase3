# Plan d'implémentation — Serveur MCP HTTP + IdP Keycloak + déploiement Docker

> **Pour un worker agentique :** SOUS-SKILL REQUIS : utiliser
> `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans`
> pour dérouler ce plan tâche par tâche. Les étapes sont en cases à cocher (`- [ ]`).

**Objectif :** un second point d'entrée du serveur MCP (`mcp_server.http_server`),
transport HTTP, qui écoute en continu et résout le profil de chaque requête depuis un
JWT Keycloak validé localement — déployable par `docker compose up` sans étape manuelle.

**Architecture :** Starlette (`RequireAuthMiddleware` + `BearerAuthBackend` +
`AuthContextMiddleware`, tous du SDK MCP) devant `StreamableHTTPSessionManager`, qui
enveloppe un `mcp.server.lowlevel.Server` propre à ce point d'entrée (même catalogue,
même `dispatch()` que le stdio, aucun changement à `mcp_server/server.py`). Deux
`SqlEngine` construits une fois au démarrage (un par profil), une `SearchEngine`
partagée. Le profil est résolu par requête via `get_access_token().claims`.

**Tech Stack :** `mcp.server.auth` + `mcp.server.streamable_http_manager` (SDK MCP,
déjà présent), `starlette` + `uvicorn` (déjà transitifs, déclarés explicitement),
`PyJWT[crypto]` (validation JWT + JWKS caché), Keycloak 26 (`quay.io/keycloak/keycloak`,
royaume importé au démarrage), Docker/docker-compose.

**Spec de référence :** `docs/spec_deploiement.md`.

## Contraintes globales

- Ligne max **100** caractères (`ruff`), cible `py311`. `mypy` doit passer sur
  `gateway ingest retrieval sql mcp_server`.
- Code en **anglais**, commentaires et docstrings en **français**.
- **Ne pas modifier** `mcp_server/server.py`, `mcp_server/catalogue.py`,
  `mcp_server/envelope.py`, `mcp_server/access.py`, `tests/acceptance/`,
  `tests/conftest.py` — ce chantier ajoute, ne remplace rien.
- Aucun test unitaire/intégration n'appelle un vrai Keycloak réseau — un `TokenVerifier`
  factice ou des JWT signés par une clé RSA de test. Seule la vérification manuelle
  (Task 5) touche un vrai conteneur Keycloak.
- Commit après **chaque** tâche, après `uv run ruff check .`, `uv run mypy gateway ingest
  retrieval sql mcp_server` et `uv run pytest tests/unit tests/integration -q` au vert.

### Deux corrections empiriques (vérifiées ce jour avec un vrai conteneur Keycloak 26.7.3, `quay.io/keycloak/keycloak:latest`) — à respecter dans les Tasks 2 et 4

1. **Chaque utilisateur du royaume importé doit avoir `email`, `firstName`, `lastName`
   remplis** (pas seulement `emailVerified: true`). Sans ça, le profil utilisateur par
   défaut de Keycloak 26 déclenche `VERIFY_PROFILE` au login, et le flow `direct grant`
   (mot de passe) échoue avec `{"error":"invalid_grant","error_description":"Account is
   not fully set up"}` — message trompeur, sans lien évident avec la cause réelle.
2. **Le token Keycloak n'a pas de claim `aud` par défaut** pour un client public sans
   mapper d'audience dédié — `jwt.decode(..., audience=...)` échoue avec
   `MissingRequiredClaimError`. Le champ fiable pour vérifier le client émetteur est
   **`azp`** (authorized party), toujours présent. Décoder avec `options={"verify_aud":
   False}`, puis comparer `claims["azp"] == audience_attendue` à la main.

---

## Task 1 : Réglages et dépendances

**Files :**
- Modify : `gateway/settings.py`
- Modify : `pyproject.toml`
- Test : `tests/unit/test_settings.py` (fichier existant, étendu)

**Interfaces :**
- Produit : `Settings.keycloak_issuer: str`, `Settings.keycloak_audience: str`,
  `Settings.http_host: str`, `Settings.http_port: int`. Consommés par
  `mcp_server/keycloak_auth.py` (Task 2) et `mcp_server/http_server.py` (Task 3).

- [ ] **Step 1 : Ajouter les dépendances explicites**

Dans `pyproject.toml`, section `dependencies`, ajouter après `"pyyaml>=6.0,<7",` :

```toml
    "pyjwt[crypto]>=2.9,<3",
    "starlette>=0.38,<1",
    "uvicorn>=0.30,<1",
```

Dans `[dependency-groups] dev`, ajouter après `"types-PyYAML>=6.0,<7",` :

```toml
    "httpx>=0.27,<1",
```

Run : `uv sync`

- [ ] **Step 2 : Étendre le test existant `tests/unit/test_settings.py`**

Regarder d'abord le fichier (`cat tests/unit/test_settings.py`) pour suivre son style
exact, puis ajouter :

```python
def test_reglages_keycloak_et_http_ont_des_valeurs_par_defaut():
    from gateway.settings import Settings

    settings = Settings(_env_file=None)
    assert settings.keycloak_issuer
    assert settings.keycloak_audience == "sorabel-gateway"
    assert settings.http_host
    assert settings.http_port > 0
```

Run : `uv run pytest tests/unit/test_settings.py -v` — attendu : échec,
`AttributeError` ou `ValidationError` (champs absents).

- [ ] **Step 3 : Ajouter les champs à `Settings`**

Dans `gateway/settings.py`, après le bloc `# --- Observabilité LLM ... ---`, ajouter :

```python
    # --- Serveur MCP HTTP + IdP Keycloak (mcp_server/http_server.py) ---
    keycloak_issuer: str = "http://localhost:8180/realms/sorabel"
    keycloak_audience: str = "sorabel-gateway"  # = azp attendu (Keycloak n'émet pas "aud"
    # par défaut pour un client public sans mapper dédié — vérifié empiriquement)
    http_host: str = "0.0.0.0"
    http_port: int = 8080
```

- [ ] **Step 4 : Lancer le test, vérifier qu'il passe**

Run : `uv run pytest tests/unit/test_settings.py -v` — attendu : PASS.

- [ ] **Step 5 : Lint et types**

Run : `uv run ruff check gateway tests/unit/test_settings.py`
Run : `uv run mypy gateway ingest retrieval sql mcp_server`

- [ ] **Step 6 : Commit**

```bash
git add pyproject.toml uv.lock gateway/settings.py tests/unit/test_settings.py
git commit -m "feat(gateway): add Keycloak/HTTP settings for the deployable MCP server"
```

---

## Task 2 : Validation JWT et résolution du profil (`mcp_server/keycloak_auth.py`)

**Files :**
- Create : `mcp_server/keycloak_auth.py`
- Test : `tests/unit/test_keycloak_auth.py`

**Interfaces :**
- Consomme : `sql.access.PROFILES`, `mcp.server.auth.provider.{TokenVerifier,
  AccessToken}` (SDK), `jwt.PyJWKClient`.
- Produit : `resolve_profile(claims: dict) -> str | None` (fonction pure),
  `KeycloakTokenVerifier` (implémente `TokenVerifier`). Consommés par
  `mcp_server/http_server.py` (Task 3).

- [ ] **Step 1 : Écrire les tests (échouent, le module n'existe pas)**

Créer `tests/unit/test_keycloak_auth.py` :

```python
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "http://localhost:18080/realms/sorabel"
AUDIENCE = "sorabel-gateway"


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _jwk(public_key, kid: str) -> dict:
    jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk["kid"] = kid
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    return jwk


def _token(private_key, kid: str, *, roles: list[str], azp: str = AUDIENCE,
           exp_delta: int = 300, issuer: str = ISSUER) -> str:
    claims = {
        "sub": "u1", "iss": issuer, "azp": azp,
        "exp": int(time.time()) + exp_delta,
        "realm_access": {"roles": roles},
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


class FakeJWKClient:
    """Remplace jwt.PyJWKClient : pas d'appel réseau, une seule clé connue."""

    def __init__(self, public_key, kid: str) -> None:
        self._jwk = jwt.PyJWK(_jwk(public_key, kid), algorithm="RS256")
        self._kid = kid

    def get_signing_key_from_jwt(self, token: str):
        header = jwt.get_unverified_header(token)
        if header.get("kid") != self._kid:
            raise jwt.exceptions.PyJWKClientError("kid inconnu")
        return self._jwk


# --- resolve_profile() : pur, sans réseau ---

def test_resolve_profile_commercial():
    from mcp_server.keycloak_auth import resolve_profile

    assert resolve_profile({"realm_access": {"roles": ["commercial", "offline_access"]}}) == "commercial"


def test_resolve_profile_support():
    from mcp_server.keycloak_auth import resolve_profile

    assert resolve_profile({"realm_access": {"roles": ["support"]}}) == "support"


def test_resolve_profile_aucun_role_connu():
    from mcp_server.keycloak_auth import resolve_profile

    assert resolve_profile({"realm_access": {"roles": ["offline_access"]}}) is None


def test_resolve_profile_les_deux_roles_ambigu():
    from mcp_server.keycloak_auth import resolve_profile

    assert resolve_profile({"realm_access": {"roles": ["commercial", "support"]}}) is None


def test_resolve_profile_claims_sans_realm_access():
    from mcp_server.keycloak_auth import resolve_profile

    assert resolve_profile({}) is None


# --- KeycloakTokenVerifier : signature, expiration, azp, kid ---

async def test_verifier_accepte_un_token_valide(rsa_keypair):
    from mcp_server.keycloak_auth import KeycloakTokenVerifier

    private_key, public_key = rsa_keypair
    token = _token(private_key, "kid1", roles=["commercial"])
    verifier = KeycloakTokenVerifier(FakeJWKClient(public_key, "kid1"), ISSUER, AUDIENCE)

    access_token = await verifier.verify_token(token)
    assert access_token is not None
    assert access_token.subject == "u1"
    assert access_token.claims["realm_access"]["roles"] == ["commercial"]


async def test_verifier_rejette_un_token_expire(rsa_keypair):
    from mcp_server.keycloak_auth import KeycloakTokenVerifier

    private_key, public_key = rsa_keypair
    token = _token(private_key, "kid1", roles=["commercial"], exp_delta=-10)
    verifier = KeycloakTokenVerifier(FakeJWKClient(public_key, "kid1"), ISSUER, AUDIENCE)

    assert await verifier.verify_token(token) is None


async def test_verifier_rejette_une_signature_invalide(rsa_keypair):
    from mcp_server.keycloak_auth import KeycloakTokenVerifier

    private_key, public_key = rsa_keypair
    autre_cle = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _token(autre_cle, "kid1", roles=["commercial"])
    verifier = KeycloakTokenVerifier(FakeJWKClient(public_key, "kid1"), ISSUER, AUDIENCE)

    assert await verifier.verify_token(token) is None


async def test_verifier_rejette_un_issuer_incorrect(rsa_keypair):
    from mcp_server.keycloak_auth import KeycloakTokenVerifier

    private_key, public_key = rsa_keypair
    token = _token(private_key, "kid1", roles=["commercial"], issuer="http://ailleurs/realms/x")
    verifier = KeycloakTokenVerifier(FakeJWKClient(public_key, "kid1"), ISSUER, AUDIENCE)

    assert await verifier.verify_token(token) is None


async def test_verifier_rejette_un_azp_incorrect(rsa_keypair):
    # azp, pas aud : Keycloak n'émet pas de claim "aud" par défaut pour un client
    # public sans mapper dédié (vérifié empiriquement contre un vrai Keycloak 26).
    from mcp_server.keycloak_auth import KeycloakTokenVerifier

    private_key, public_key = rsa_keypair
    token = _token(private_key, "kid1", roles=["commercial"], azp="un-autre-client")
    verifier = KeycloakTokenVerifier(FakeJWKClient(public_key, "kid1"), ISSUER, AUDIENCE)

    assert await verifier.verify_token(token) is None


async def test_verifier_rejette_un_kid_inconnu(rsa_keypair):
    from mcp_server.keycloak_auth import KeycloakTokenVerifier

    private_key, public_key = rsa_keypair
    token = _token(private_key, "kid-inattendu", roles=["commercial"])
    verifier = KeycloakTokenVerifier(FakeJWKClient(public_key, "kid1"), ISSUER, AUDIENCE)

    assert await verifier.verify_token(token) is None
```

Run : `uv run pytest tests/unit/test_keycloak_auth.py -v`
Attendu : `ModuleNotFoundError: No module named 'mcp_server.keycloak_auth'`

- [ ] **Step 2 : Implémenter `mcp_server/keycloak_auth.py`**

```python
"""Validation JWT Keycloak et résolution du profil Sorabel — chantier déploiement HTTP.

``resolve_profile`` est une fonction pure (pas de réseau, pas d'objet Keycloak) :
elle lit ``realm_access.roles`` et retourne le profil Sorabel s'il y en a exactement
un reconnu, ``None`` sinon (rôle absent ou ambigu — un jeton valide mais dont
l'identité ne peut pas être rattachée à un profil, pas une erreur d'authentification).

``KeycloakTokenVerifier`` implémente le ``Protocol TokenVerifier`` du SDK MCP
(``mcp.server.auth.provider``) : signature JWT validée contre le JWKS Keycloak (caché
par ``jwt.PyJWKClient``, pas un appel réseau par requête), expiration, issuer, et
``azp`` (authorized party) — pas ``aud`` : Keycloak n'émet pas de claim ``aud`` par
défaut pour un client public sans mapper d'audience dédié (vérifié empiriquement
contre un vrai Keycloak 26.7.3, spec_deploiement.md § corrections empiriques).
"""

from __future__ import annotations

import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

from sql.access import PROFILES

#: Réciproque : ce module ne connaît qu'un rôle réalm par profil Sorabel, mapping
#: direct (conception § 3.2 de la vue générale : « IDE dev passe par
#: Commercial/Support », pas un troisième profil).
_KNOWN_ROLES = frozenset(PROFILES)


def resolve_profile(claims: dict) -> str | None:
    roles = set(claims.get("realm_access", {}).get("roles", []))
    matched = roles & _KNOWN_ROLES
    if len(matched) != 1:
        return None
    return next(iter(matched))


class KeycloakTokenVerifier(TokenVerifier):
    def __init__(self, jwks_client, issuer: str, audience: str) -> None:
        self._jwks_client = jwks_client
        self._issuer = issuer
        self._audience = audience

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key, algorithms=["RS256"], issuer=self._issuer,
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return None
        if claims.get("azp") != self._audience:
            return None
        return AccessToken(
            token=token, client_id=claims["azp"], scopes=[], expires_at=claims["exp"],
            subject=claims.get("sub"), claims=claims,
        )
```

- [ ] **Step 3 : Lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_keycloak_auth.py -v`
Attendu : 12 PASS.

- [ ] **Step 4 : Lint et types**

Run : `uv run ruff check mcp_server tests/unit/test_keycloak_auth.py`
Run : `uv run mypy gateway ingest retrieval sql mcp_server`

- [ ] **Step 5 : Commit**

```bash
git add mcp_server/keycloak_auth.py tests/unit/test_keycloak_auth.py
git commit -m "feat(mcp_server): validate Keycloak JWTs and resolve the Sorabel profile"
```

---

## Task 3 : Serveur HTTP (`mcp_server/http_server.py`)

**Files :**
- Create : `mcp_server/http_server.py`
- Test : `tests/integration/test_http_server.py`

**Interfaces :**
- Consomme : `mcp_server.keycloak_auth.{resolve_profile, KeycloakTokenVerifier}`
  (Task 2), `mcp_server.catalogue.{SERVER_INSTRUCTIONS, build_tools}`,
  `mcp_server.envelope.Envelope`, `mcp_server.server.dispatch` (déjà transport-agnostic,
  chantier MCP précédent), `mcp_server.access.YamlAccessRules`.
- Produit : `build_app(...) -> Starlette` (factory testable sans réseau), `main()`
  (point d'entrée `python -m mcp_server.http_server`).

**Prérequis pour les tests** : `make seed` (base réelle) doit avoir tourné ; Chroma
(`make up`) doit être joignable pour construire la `SearchEngine`.

- [ ] **Step 1 : Écrire le test d'intégration (échoue, le module n'existe pas)**

Créer `tests/integration/test_http_server.py` :

```python
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
from jwt.algorithms import RSAAlgorithm
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


@pytest.fixture(scope="module")
def app():
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
```

Run : `uv run pytest tests/integration/test_http_server.py -v`
Attendu : `ModuleNotFoundError: No module named 'mcp_server.http_server'`

- [ ] **Step 2 : Implémenter `mcp_server/http_server.py`**

```python
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
        profile = resolve_profile(access_token.claims) if access_token else None
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
    journal_path = os.environ.get("GATEWAY_JOURNAL", "logs/mcp_audit.jsonl")
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
```

- [ ] **Step 3 : Lancer le test d'intégration**

Prérequis : `make seed` si absent, `make up` (Chroma).
Run : `uv run pytest tests/integration/test_http_server.py -v`
Attendu : 4 PASS. Si `test_sans_token_refuse_401` échoue différemment (pas
d'exception), vérifier que `streamablehttp_client` propage bien l'erreur HTTP du
premier POST — sinon adapter l'assertion à l'exception réellement observée plutôt que
d'assouplir le test.

- [ ] **Step 4 : Lint et types**

Run : `uv run ruff check mcp_server tests/integration/test_http_server.py`
Run : `uv run mypy gateway ingest retrieval sql mcp_server`

- [ ] **Step 5 : Commit**

```bash
git add mcp_server/http_server.py tests/integration/test_http_server.py
git commit -m "feat(mcp_server): add an HTTP entry point authenticated via Keycloak JWTs"
```

---

## Task 4 : Docker — Keycloak, image gateway, provisionnement automatique

**Files :**
- Create : `docker/keycloak/sorabel-realm.json`
- Create : `docker/entrypoint.sh`
- Create : `scripts/ensure_ingested.py`
- Create : `Dockerfile`
- Modify : `docker-compose.yml`

**Interfaces :**
- Consomme : `ingest.pipeline.ingest_corpus` (déjà utilisé par `scripts/run_ingest.py`,
  pas dupliqué), `scripts/seed.py::main`.

- [ ] **Step 1 : Royaume Keycloak, avec les champs vérifiés nécessaires**

Créer `docker/keycloak/sorabel-realm.json` :

```json
{
  "realm": "sorabel",
  "enabled": true,
  "sslRequired": "none",
  "roles": {
    "realm": [
      {"name": "commercial"},
      {"name": "support"}
    ]
  },
  "clients": [
    {
      "clientId": "sorabel-gateway",
      "enabled": true,
      "publicClient": true,
      "directAccessGrantsEnabled": true,
      "standardFlowEnabled": false,
      "protocol": "openid-connect"
    }
  ],
  "users": [
    {
      "username": "commercial-demo",
      "enabled": true,
      "email": "commercial-demo@sorabel.example",
      "emailVerified": true,
      "firstName": "Commercial",
      "lastName": "Demo",
      "requiredActions": [],
      "credentials": [{"type": "password", "value": "demo", "temporary": false}],
      "realmRoles": ["commercial"]
    },
    {
      "username": "support-demo",
      "enabled": true,
      "email": "support-demo@sorabel.example",
      "emailVerified": true,
      "firstName": "Support",
      "lastName": "Demo",
      "requiredActions": [],
      "credentials": [{"type": "password", "value": "demo", "temporary": false}],
      "realmRoles": ["support"]
    }
  ]
}
```

`email`/`firstName`/`lastName` sont **obligatoires** ici — sans eux, Keycloak 26
déclenche `VERIFY_PROFILE` au login et le flow mot de passe échoue avec un message
trompeur (vérifié empiriquement, voir Contraintes globales en tête de ce plan).

- [ ] **Step 2 : Script de provisionnement idempotent de l'index Chroma**

Créer `scripts/ensure_ingested.py` :

```python
"""Ingère le corpus si (et seulement si) l'index Chroma est vide.

Distinct de scripts/run_ingest.py (qui ingère toujours) : celui-ci est fait pour
tourner à chaque démarrage du conteneur gateway (docker/entrypoint.sh), sans jamais
ré-ingérer un index déjà peuplé.
"""

from gateway.chroma import chroma_client, open_collection
from gateway.embedder import AzureEmbedder
from gateway.settings import get_settings
from ingest.pipeline import ingest_corpus


def main() -> None:
    settings = get_settings()
    collection = open_collection(chroma_client(settings), settings.chroma_collection)
    if collection.count() > 0:
        print(f"« {settings.chroma_collection} » déjà peuplée "
              f"({collection.count()} chunks) — rien à faire")
        return
    written = ingest_corpus(settings.corpus_dir, collection, AzureEmbedder(settings))
    print(f"{written} chunks ingérés dans « {settings.chroma_collection} »")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3 : Point d'entrée du conteneur gateway**

Créer `docker/entrypoint.sh` :

```bash
#!/bin/sh
set -e

[ -f data/sorabel.db ] || uv run python scripts/seed.py
uv run python scripts/ensure_ingested.py

exec uv run python -m mcp_server.http_server
```

Rendre exécutable : `chmod +x docker/entrypoint.sh`

- [ ] **Step 4 : `Dockerfile` de l'image gateway**

Créer `Dockerfile` (racine du dépôt) :

```dockerfile
FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
RUN chmod +x docker/entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["docker/entrypoint.sh"]
```

- [ ] **Step 5 : Étendre `docker-compose.yml`**

Ajouter à `docker-compose.yml` (après le service `chroma`) :

```yaml
  keycloak:
    image: quay.io/keycloak/keycloak:26.7
    command: ["start-dev", "--import-realm"]
    environment:
      - KC_BOOTSTRAP_ADMIN_USERNAME=admin
      - KC_BOOTSTRAP_ADMIN_PASSWORD=admin
      - KC_HOSTNAME=localhost
      - KC_HOSTNAME_PORT=8180
      - KC_HTTP_ENABLED=true
    ports:
      - "8180:8080"
    volumes:
      - ./docker/keycloak:/opt/keycloak/data/import
    healthcheck:
      test: ["CMD", "bash", "-c", "exec 3<>/dev/tcp/localhost/8080 && echo ok"]
      interval: 5s
      timeout: 3s
      retries: 24

  gateway:
    build: .
    ports:
      - "8090:8080"
    volumes:
      - ./data/corpus:/app/data/corpus:ro
      - ./data:/app/data
      - ./logs:/app/logs
    env_file: .env
    environment:
      - CHROMA_URL=http://chroma:8000
      - KEYCLOAK_ISSUER=http://localhost:8180/realms/sorabel
      - HTTP_HOST=0.0.0.0
      - HTTP_PORT=8080
    extra_hosts:
      - "localhost:host-gateway"
    depends_on:
      chroma:
        condition: service_healthy
      keycloak:
        condition: service_healthy
```

`extra_hosts: localhost:host-gateway` : le conteneur `gateway` doit valider les jetons
avec le **même** `issuer` que celui inscrit dedans par Keycloak (`http://localhost:8180/
realms/sorabel`, l'adresse que tout client externe utilise) — sans ça, la validation
JWT échouerait sur un `issuer` différent entre client et serveur. `host-gateway` est le
mécanisme Docker standard (Docker Desktop et Docker Engine ≥ 20.10 sur Linux) pour
qu'un conteneur résolve `localhost` vers la machine hôte.

- [ ] **Step 6 : Vérification syntaxique sans tout démarrer**

Run : `docker compose config -q` — attendu : aucune erreur (juste une validation de
syntaxe/interpolation, ne démarre rien).

- [ ] **Step 7 : Commit**

```bash
git add docker/ scripts/ensure_ingested.py Dockerfile docker-compose.yml
git commit -m "feat(deploy): add Keycloak, a gateway image, and auto-provisioning"
```

---

## Task 5 : Vérification manuelle de bout en bout + documentation

**Files :**
- Modify : `sorabel_phase3/README.md` (déjà mis à jour au chantier précédent — ajouter
  la section déploiement Docker)
- Modify : `.env.example` (ajout `KEYCLOAK_ISSUER`, `HTTP_HOST`, `HTTP_PORT`)

Aucun test automatisé dans cette tâche — vérification en conditions réelles, seule
façon de confirmer que Docker/Keycloak/Chroma/gateway s'assemblent correctement, et
mise à jour de la doc utilisateur.

- [ ] **Step 1 : Ajouter les nouvelles variables à `.env.example`**

```
# Serveur MCP HTTP + IdP Keycloak (mcp_server/http_server.py, déploiement Docker)
KEYCLOAK_ISSUER=http://localhost:8180/realms/sorabel
HTTP_HOST=0.0.0.0
HTTP_PORT=8080
```

- [ ] **Step 2 : Démarrage complet**

```bash
docker compose down -v   # repartir propre si des volumes précédents existent
docker compose up --build
```

Attendu, dans l'ordre des logs : Chroma healthy, Keycloak importe le royaume `sorabel`
("Realm 'sorabel' imported"), `gateway` provisionne (`seed` + `ensure_ingested`) puis
démarre uvicorn sur `0.0.0.0:8080`.

- [ ] **Step 3 : Récupérer un vrai token et appeler la gateway**

```bash
TOKEN=$(curl -s -X POST http://localhost:8180/realms/sorabel/protocol/openid-connect/token \
  -d "client_id=sorabel-gateway" -d "grant_type=password" \
  -d "username=commercial-demo" -d "password=demo" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s -X POST http://localhost:8090/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-06-18","capabilities":{},
                  "clientInfo":{"name":"curl","version":"0"}}}'
```

Attendu : une réponse JSON-RPC `result` (pas d'erreur 401/500). Si 401 : vérifier que
le conteneur `gateway` résout bien `localhost:8180` (Step 5 de la Task 4,
`extra_hosts`) — `docker compose exec gateway getent hosts localhost` doit résoudre
vers l'IP de l'hôte, pas `127.0.0.1` interne au conteneur.

- [ ] **Step 4 : Refaire le test avec `support-demo`, vérifier le filtrage des
  colonnes sensibles sur `get_schema`** (même principe que Step 3, `username=support-demo`).

- [ ] **Step 5 : Documenter dans le README**

Dans `sorabel_phase3/README.md`, section `## Démarrage`, ajouter après le bloc
`make ui-gateway` existant :

````markdown
### Déploiement complet (HTTP + Keycloak)

```bash
docker compose up --build
```

Démarre Chroma, Keycloak (royaume `sorabel` importé automatiquement, deux comptes de
démo `commercial-demo`/`support-demo`, mot de passe `demo`) et la gateway HTTP
(`http://localhost:8090/mcp`), provisionnée automatiquement (base + index) au premier
démarrage. Voir `docs/spec_deploiement.md` pour le détail. **Royaume de démo
uniquement** — mots de passe en clair dans `docker/keycloak/sorabel-realm.json`,
jamais pour un déploiement réel.
````

- [ ] **Step 6 : Commit**

```bash
git add .env.example sorabel_phase3/README.md
git commit -m "docs: document the Docker + Keycloak deployment"
```

---

## Self-review

**Couverture de la spec** : § 3.2 (KeycloakTokenVerifier) -> Task 2 ; § 3.3
(http_server.py, deux SqlEngine) -> Task 3 ; § 3.4 (royaume Keycloak) -> Task 4 Step 1 ;
§ 3.5 (provisionnement auto) -> Task 4 Steps 2-3 ; § 4 (décisions : stateless, un rôle
= un profil, docker-compose) -> Tasks 3-4 ; § 5 (tests) -> Tasks 2-3 (unitaire +
intégration), Task 5 (manuel) ; § 6 (hors périmètre) -> non traité, conforme.

**Corrections empiriques** (non prévues dans la spec, découvertes en vérifiant avec un
vrai Keycloak avant d'écrire ce plan) intégrées : profil utilisateur complet requis
(Task 4 Step 1), validation par `azp` et non `aud` (Tasks 2-3).

**Cohérence des types** : `KeycloakTokenVerifier.verify_token` (Task 2) retourne
`AccessToken | None`, consommé par `get_access_token()` (SDK) dans le `call_tool`
handler de `build_app` (Task 3) — `access_token.claims` est le seul champ lu, cohérent
avec ce que `resolve_profile` (Task 2) attend. `build_app(...)` (Task 3) est le seul
point de construction du serveur HTTP, utilisé identiquement par `main()` (production)
et le test d'intégration (Task 3) — pas de deuxième chemin de construction parallèle.
