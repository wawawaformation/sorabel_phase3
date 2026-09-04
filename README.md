# Sorabel Data Gateway

Point d'accès unique aux données de **Sorabel**, distributeur B2B de matériel électrique et d'outillage professionnel. La gateway expose, via un **serveur MCP**, le corpus documentaire (fiches techniques, notices, procédures SAV, notes internes) et la base SQL (produits, stocks, commandes, clients, ventes) à tous les outils internes — bot Slack du support, IDE des développeurs, poste des commerciaux — sous une gouvernance commune : matrice d'accès par profil, lecture seule stricte côté SQL, journal de tous les appels.

## Features

- Recherche documentaire avancée sur le corpus : dense + lexicale (hybride), reranking, réponses sourcées (titre + référence + date), refus explicite hors corpus
- Accès aux données en langage naturel : génération SQL lecture seule, périmètre de colonnes par profil, SQL tracé (jamais renvoyé au client, spec_mcp.md § 4.1)
- Tools figés pour les besoins récurrents : `check_stock`, `order_status`
- Serveur MCP unique exposant le catalogue de 8 tools, matrice d'accès par profil (`support`, `commercial`) avec journalisation de chaque appel
- Données en place : base SQL générée par `scripts/seed.py`, corpus de ~400 documents indexé dans Chroma via docker compose
- Client MCP de test jouable avec les deux profils (`scripts/mcp_client.py`), interface Streamlit de démo bout en bout (`app_gateway.py`)

## Contrat d'intégration

`docs/spec_mcp.md` fixe le contrat implémenté : matrice réduite aux 3 colonnes SQL
sensibles (aucun tool ni collection RAG n'est restreint, `docs/spec_retrieval.md` §
« hors périmètre »), enveloppe double (`CallToolResult.isError`/`_meta` + JSON
`{status, payload, message}`), SQL jamais renvoyé au client. La suite
`tests/acceptance/` consomme la gateway en boîte noire, exactement comme un client
interne, et passe intégralement contre ce contrat.

## Stack

- Python 3.11 (géré avec `uv`)
- Chroma pour l'index vectoriel (`docker compose`, port 8002)
- SQLite pour la base (`data/sorabel.db`, générée par le seed, à ouvrir en lecture seule)
- SDK MCP (`mcp`) pour le serveur et le client stdio
- `pypdf` / `beautifulsoup4` pour l'extraction du corpus, `rank-bm25` pour la piste lexicale
- Embeddings via Azure AI Foundry (`openai`, endpoint compatible `/openai/v1`) — pas de modèle local

```bash
uv sync                       # cœur + outils de dev
```

## Démarrage

```bash
make install      # uv sync
make seed         # génère data/sorabel.db (déterministe, aligné sur le corpus)
make up           # docker compose : Chroma sur localhost:8002
make test         # suite d'acceptance (unit + intégration + acceptance)
make serve        # serveur MCP stdio (profil via SORABEL_PROFILE)
make client       # client de test (PROFILE=support|commercial)
make ui-gateway   # démo Streamlit passant par le vrai serveur MCP
```

Exemples côté client :

```bash
uv run python scripts/mcp_client.py --profile support --tool search_docs --args '{"query": "REF-8842"}'
uv run python scripts/mcp_client.py --profile commercial --tool ask_database --args '{"question": "combien de commandes en avril ?"}'
```

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

Exemple d'appel (récupère un token Keycloak puis appelle la gateway) :

```bash
TOKEN=$(curl -s -X POST http://localhost:8180/realms/sorabel/protocol/openid-connect/token \
  -d "client_id=sorabel-gateway" -d "grant_type=password" \
  -d "username=commercial-demo" -d "password=demo" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s -X POST http://localhost:8090/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"check_stock","arguments":{"ref":"REF-8842"}}}'
```

## Layout

```
data/
  corpus/             # ~400 documents : fiches/ notices/ (PDF), sav/ (HTML), notes/ (Markdown)
  sorabel.db          # base SQL (hors git — générée par make seed, schéma dans docs/schema.sql)
docs/
  schema.sql          # schéma commenté de la base (colonnes sensibles signalées)
eval/
  questions_rag.jsonl # questions documentaires : couvertes, hors corpus, par référence exacte
  questions_sql.jsonl # questions métier en langage naturel, dont cas limites
ingest/               # chaîne d'ingestion du corpus
retrieval/            # recherche documentaire hybride (SearchEngine)
sql/                  # accès SQL en langage naturel (SqlEngine)
mcp_server/           # serveur MCP de la gateway
  server.py           # stdio, profil via SORABEL_PROFILE (mode démo, cible tests/acceptance)
  http_server.py       # HTTP, profil résolu par requête via un JWT Keycloak (déploiement)
scripts/
  seed.py             # génère et peuple data/sorabel.db
  mcp_client.py       # client MCP de test (profils support / commercial)
  ensure_ingested.py  # ingestion idempotente, appelée au démarrage du conteneur gateway
app_gateway.py        # démo Streamlit du serveur MCP complet
docker/
  entrypoint.sh       # point d'entrée du conteneur gateway (seed + ingest + serveur HTTP)
  keycloak/sorabel-realm.json  # royaume de démo importé au démarrage de Keycloak
Dockerfile             # image du service gateway (docker-compose)
tests/acceptance/     # suite d'acceptance boîte noire, adossée aux exigences E1–E6
```
