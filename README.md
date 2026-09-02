# Sorabel Data Gateway

Point d'accès unique aux données de **Sorabel**, distributeur B2B de matériel électrique et d'outillage professionnel. La gateway expose, via un **serveur MCP**, le corpus documentaire (fiches techniques, notices, procédures SAV, notes internes) et la base SQL (produits, stocks, commandes, clients, ventes) à tous les outils internes — bot Slack du support, IDE des développeurs, poste des commerciaux — sous une gouvernance commune : matrice d'accès par profil, lecture seule stricte côté SQL, journal de tous les appels.

## Features

- Recherche documentaire avancée sur le corpus : dense + lexicale (hybride), reranking, réponses sourcées (titre + référence + date), refus explicite hors corpus (à construire)
- Accès aux données en langage naturel : génération SQL lecture seule, périmètre de tables par profil, requête toujours renvoyée avec le résultat (à construire)
- Tools figés pour les besoins récurrents : `check_stock`, `order_status` (à construire)
- Serveur MCP unique exposant tout le catalogue, sous matrice d'accès par profil (`support`, `commercial`) avec journalisation de chaque appel (à construire)
- Données en place : base SQL générée par `scripts/seed.py`, corpus de ~400 documents, Chroma prête via docker compose (index encore vide)
- Client MCP de test jouable avec les deux profils (`scripts/mcp_client.py`)

## Contrat d'intégration

Contrat d'intégration non fourni à ce stade — exigences E1–E6, matrice d'accès
et enveloppes (réponse, journal) restent à définir en conception avant
implémentation. La suite `tests/acceptance/` consomme la gateway en boîte
noire, exactement comme un client interne : elle est rouge tant que le
serveur et ses tools ne tiennent pas ce contrat, une fois fixé.

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
make test         # suite d'acceptance (rouge tant que la gateway n'est pas construite)
make serve        # serveur MCP stdio (profil via SORABEL_PROFILE)
make client       # client de test (PROFILE=support|commercial)
```

Exemples côté client :

```bash
uv run python scripts/mcp_client.py --profile support --tool search_docs --args '{"query": "REF-8842"}'
uv run python scripts/mcp_client.py --profile commercial --tool ask_database --args '{"question": "combien de commandes en avril ?"}'
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
ingest/               # chaîne d'ingestion du corpus (à concevoir et construire)
retrieval/            # recherche documentaire (à concevoir et construire)
sql/                  # accès SQL en langage naturel (à concevoir et construire)
mcp_server/           # serveur MCP de la gateway (à concevoir et construire)
scripts/
  seed.py             # génère et peuple data/sorabel.db
  mcp_client.py       # client MCP de test (profils support / commercial)
tests/acceptance/     # suite d'acceptance boîte noire, adossée aux exigences E1–E6
```
