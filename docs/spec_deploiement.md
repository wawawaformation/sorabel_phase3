# Spécification — Serveur MCP déployable : transport HTTP, IdP Keycloak, Docker

> Extension au-delà du brief (`brief/brief.md` ne mentionne que 2 profils, pas d'IdP —
> vérifié). Réalise ce que `conception/vue_generale.drawio` § « 1. CLIENTS & IDENTITE »
> décrivait déjà : un serveur unique, OAuth 2.0/OIDC, validation JWT locale (JWKS en
> cache). Le mode stdio + `SORABEL_PROFILE` (`docs/spec_mcp.md`) était une simplification
> de démo assumée pour les 6 jours du brief — cette spec construit ce qui avait été
> différé, sans y toucher.

## 1. Objectif

Un serveur MCP qui **écoute en continu** (transport HTTP), accepte des connexions de
plusieurs clients simultanés, et résout le profil de chacun depuis un **vrai jeton
d'identité** (JWT Keycloak), pas depuis une variable d'environnement lue une fois au
démarrage. Déployable par `docker compose up` sur n'importe quelle machine Docker, sans
étape manuelle de provisionnement.

**Client "IDE dev"** (`vue_generale.drawio`) : ce n'est pas un troisième profil Sorabel —
c'est un type de client qui s'authentifie comme `commercial` ou `support` selon
l'utilisateur, exactement comme les deux autres clients. Toujours deux profils.

**Ne change pas** : `mcp_server/server.py` (stdio, `SORABEL_PROFILE`), la suite
`tests/acceptance/` qui le cible, `retrieval/`, `sql/`, la matrice d'accès
(`mcp_server/matrice_acces.yaml`), le catalogue de tools (`mcp_server/catalogue.py`),
l'enveloppe (`mcp_server/envelope.py`). Ce chantier ajoute un second point d'entrée, il
ne remplace pas le premier.

---

## 2. Ce que le SDK MCP installé fournit déjà (vérifié empiriquement, `mcp==1.x`)

Le SDK a un support natif du rôle *resource server* OAuth 2.0 (RFC 9728), pas seulement
le transport HTTP brut :

```text
mcp.server.auth.provider.TokenVerifier          Protocol : verify_token(token) -> AccessToken | None
mcp.server.auth.provider.AccessToken             token, client_id, scopes, expires_at, subject, claims
mcp.server.auth.middleware.bearer_auth           BearerAuthBackend, RequireAuthMiddleware
mcp.server.auth.middleware.auth_context          get_access_token() — accesseur par requête (contextvar)
mcp.server.streamable_http_manager               StreamableHTTPSessionManager(app: Server, stateless=True)
```

`get_access_token()` est utilisable **depuis le handler `call_tool` existant**, sans
changer sa signature : le jeton validé de la requête en cours est dans un contextvar,
posé par le middleware avant que le handler ne s'exécute.

`PyJWT` (`jwt`, déjà présent) fournit `PyJWKClient(uri, cache_jwk_set=True, lifespan=300)`
— JWKS caché, rafraîchi toutes les 5 minutes, pas un appel réseau par requête (conception
§ 1.5). `starlette` et `uvicorn` sont déjà présents (dépendances du SDK MCP). Aucune
nouvelle dépendance lourde à ajouter — seul `PyJWT` passe de transitif à direct dans
`pyproject.toml`, pour ne pas dépendre d'un import silencieux.

---

## 3. Architecture

```text
Client (commercial / support / dev, via IDE)
        │  HTTP POST /mcp
        │  Authorization: Bearer <JWT Keycloak>
        ▼
Starlette : RequireAuthMiddleware(BearerAuthBackend(KeycloakTokenVerifier))
        │  JWT invalide/expiré/signature fausse -> 401, jamais notre code
        ▼
AuthContextMiddleware — pose le AccessToken validé dans le contextvar
        ▼
StreamableHTTPSessionManager(server)  ── même Server (mcp.server.lowlevel) que le stdio
        │
        ▼
call_tool handler (mcp_server/http_server.py)
        │  get_access_token().claims -> resolve_profile() -> "commercial" | "support" | None
        │  profil introuvable (rôle absent/ambigu) -> refus FORBIDDEN (enveloppe existante)
        ▼
dispatch()  ── INCHANGÉ (mcp_server/server.py), reçoit déjà profile/search_engine/sql_engine
```

### 3.1 Modules

```text
mcp_server/
├── keycloak_auth.py     KeycloakTokenVerifier (implémente TokenVerifier), resolve_profile()
├── http_server.py        point d'entrée `python -m mcp_server.http_server`
Dockerfile                 image du service gateway
docker/
├── entrypoint.sh          provisionnement auto (seed + ingest si absents) puis exec
└── keycloak/
    └── sorabel-realm.json export du royaume : client, 2 rôles, utilisateurs de démo
docker-compose.yml          + services `keycloak`, `gateway`
```

### 3.2 `KeycloakTokenVerifier`

```python
class KeycloakTokenVerifier:  # implémente mcp.server.auth.provider.TokenVerifier
    def __init__(self, jwks_client: PyJWKClient, issuer: str, audience: str): ...
    async def verify_token(self, token: str) -> AccessToken | None:
        # 1. signing_key = jwks_client.get_signing_key_from_jwt(token)  (caché, kid)
        # 2. claims = jwt.decode(token, signing_key.key, algorithms=["RS256"],
        #                        audience=audience, issuer=issuer)
        #    -> None si signature/expiration/issuer/audience invalide (authentification)
        # 3. AccessToken(token=token, client_id=claims["azp"], scopes=[],
        #                expires_at=claims["exp"], subject=claims["sub"], claims=claims)
```

`resolve_profile(claims: dict) -> str | None` — fonction pure, testable sans réseau :
lit `claims["realm_access"]["roles"]`, retourne `"commercial"` ou `"support"` si l'un des
deux (et un seul) y figure, sinon `None`. Le `None` n'est **pas** un échec
d'authentification (le jeton est valide) — c'est un refus d'autorisation, traité par
`http_server.py` via l'enveloppe `FORBIDDEN` existante, pas par le 401 de la couche
OAuth. Distinction déjà actée (`docs/spec_mcp.md` § conception, « authentification
invalide reste à la couche OAuth/HTTP, profil non autorisé = refus contrôlé du tool »).

### 3.3 `mcp_server/http_server.py`

Construit **une fois au démarrage** :
- une `SearchEngine` (RAG ouvert aux deux profils, aucune distinction à faire) ;
- **deux** `SqlEngine`, un par profil (`{"commercial": SqlEngine(...), "support":
  SqlEngine(...)}`) — chacun avec ses propres connexions SQLite et son `AccessRules`,
  exactement comme le ferait un process stdio dédié, mais les deux vivent dans le même
  process HTTP.

Le `call_tool` handler diffère du stdio uniquement sur la résolution du profil :

```python
@server.call_tool()
async def _call_tool(name: str, arguments: dict) -> types.CallToolResult:
    access_token = get_access_token()  # posé par AuthContextMiddleware
    profile = resolve_profile(access_token.claims)
    if profile is None:
        return Envelope("refused", {}, "Profil non résolu pour cette identité.",
                         "FORBIDDEN").to_call_tool_result()
    return await dispatch(
        name, arguments, profile=profile, search_engine=search_engine,
        sql_engine=sql_engines[profile], llm_client=llm_client, settings=settings,
        trace=trace,
    )
```

`dispatch()` et `_run()` (`mcp_server/server.py`) ne changent pas — ils sont déjà
transport-agnostic, c'est tout leur intérêt (spec_mcp.md § 3, séparation dispatch/main).

### 3.4 Royaume Keycloak (`docker/keycloak/sorabel-realm.json`)

- Royaume `sorabel`, client confidentiel `sorabel-gateway`.
- Deux rôles réalm : `commercial`, `support` (un rôle = un profil, mapping direct, pas
  de table de correspondance à maintenir).
- Deux utilisateurs de démo : `commercial-demo` (rôle `commercial`),
  `support-demo` (rôle `support`) — mots de passe de démo, non secrets réels.
  Un client IDE s'authentifie avec l'un de ces deux comptes selon le profil voulu.
- Importé automatiquement au démarrage du conteneur Keycloak
  (`start-dev --import-realm`, image officielle `quay.io/keycloak/keycloak`).

### 3.5 Provisionnement automatique (`docker/entrypoint.sh`)

```bash
#!/bin/sh
set -e
[ -f data/sorabel.db ] || uv run python scripts/seed.py
uv run python scripts/ensure_ingested.py   # no-op si la collection Chroma n'est pas vide
exec uv run python -m mcp_server.http_server
```

`scripts/ensure_ingested.py` : ouvre la collection Chroma (déjà joignable, `chroma` est
`service_healthy` avant que `gateway` démarre — `depends_on` dans `docker-compose.yml`,
sur `chroma` et `keycloak`) et appelle `collection.count()` ; si `0`, lance
`ingest.pipeline.ingest_corpus` (la fonction déjà utilisée par `scripts/run_ingest.py`,
pas dupliquée). Idempotent : un second `docker compose up` ne réingère rien.

---

## 4. Décisions

- **Transport HTTP (Streamable HTTP), pas SSE ni stdio** — seul moyen d'avoir un
  process serveur unique acceptant plusieurs clients simultanés avec une identité par
  requête (contrainte du modèle stdio actuel : un profil résolu une fois au lancement
  du process).
- **`stateless=True`** sur `StreamableHTTPSessionManager` — pas de session MCP
  persistante côté serveur à gérer entre deux requêtes ; chaque appel HTTP est
  indépendant. Suffisant ici (pas de besoin de session longue), évite un store de
  session à opérer en production.
- **Un rôle réalm = un profil**, mapping direct sans table de correspondance — le plus
  simple qui satisfasse le besoin, cohérent avec « aucune permission n'est écrite deux
  fois » (déjà un principe de la matrice).
- **Deux `SqlEngine` construits une fois au démarrage**, pas par requête — les
  connexions SQLite et l'authorizer sont coûteux à ouvrir, et le profil ne change pas
  entre deux requêtes du même rôle. Cohérent avec le modèle stdio (un `SqlEngine` par
  profil), juste les deux vivent maintenant dans un seul process au lieu de deux.
- **`docker-compose.yml` reste le seul mécanisme de déploiement visé** — pas de
  Kubernetes, pas de terraform : hors périmètre, disproportionné pour ce projet.
- **TLS/HTTPS, rotation de secrets, haute disponibilité : hors périmètre.** Le royaume
  Keycloak de démo n'est pas un modèle de sécurité de production (mots de passe en
  clair dans un fichier versionné) — acceptable pour une démo locale/formation, à
  documenter explicitement comme tel dans le README.

---

## 5. Tests

- **Unitaire** (`tests/unit/test_keycloak_auth.py`) : `resolve_profile()` avec des
  claims construits à la main (rôle commercial, support, aucun, les deux, absent) — pas
  de réseau. `KeycloakTokenVerifier.verify_token()` avec des JWT signés par une paire de
  clés RSA de test (générée dans le test, pas Keycloak) : token valide, expiré,
  signature fausse, issuer/audience incorrects, `kid` inconnu du JWKS.
- **Intégration** (`tests/integration/test_http_server.py`) : l'app Starlette montée en
  mémoire (`httpx.ASGITransport`, pas de process réseau), un `TokenVerifier` factice
  injecté (pas Keycloak réel) — appel `tools/call` avec profil commercial, avec profil
  support, sans token (401), avec token valide mais rôle absent (`FORBIDDEN`).
- **Manuel** : `docker compose up`, récupérer un jeton réel via le flow `password` de
  Keycloak pour `commercial-demo`/`support-demo`, appeler `/mcp` avec `curl` ou
  `scripts/mcp_client.py` adapté au transport HTTP (ou un script dédié
  `scripts/mcp_http_client.py`).
- **Acceptance existante** : inchangée, ne teste que le mode stdio.

---

## 6. Hors périmètre (explicite)

- Remplacer ou faire cohabiter le stdio avec un vrai flux d'autorisation OAuth
  complet (`authorization_code`, refresh tokens côté client) — on est *resource
  server* uniquement, Keycloak reste l'*authorization server* complet, notre code ne
  gère jamais de mot de passe.
- Filtrage dynamique de `tools/list` par profil (déjà écarté en conception § 2.2, MVP).
- Scalabilité horizontale (plusieurs instances `gateway` derrière un load balancer) —
  le mode stateless le permettrait en théorie, non vérifié empiriquement ici.
