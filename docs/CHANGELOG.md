# Changelog — implémentation

Tâches réalisées, plus récentes en premier. Décisions de conception : voir `conception/` et son propre `CHANGELOG.md` (racine du projet).

## 2026-09-03 — Text-to-SQL opérationnel : génération, barrières, tools figés

- `sql/` construit selon `docs/spec_sql.md` et `docs/plan_sql.md` (11 tâches, TDD) :
  `access.py` (règles d'accès injectées), `descriptions.py` (documentation métier
  transposée de `docs/schema.sql`), `schema.py` (introspection `PRAGMA` + correspondance
  mois/millésime), `guard.py` (connexions, authorizer, validation, `LIMIT`, délai),
  `generate.py` (génération structurée + détection d'écriture), `tools.py` (`check_stock`,
  `order_status`), `trace.py` (journal + alerte dupliquée), `engine.py` (`SqlEngine`,
  orchestration).
- `SqlEngine(profile, access_rules, trace, llm_client, settings)` : profil et règles
  d'accès **injectés au constructeur**, jamais en paramètre de méthode — même principe que
  `SearchEngine` pour le RAG, ce qui rend le profil non falsifiable par un appelant de tool.
- **Deux connexions SQLite aux rôles séparés**, découverte faite en vérifiant la spec
  contre le vrai moteur : `PRAGMA` déclenche `SQLITE_PRAGMA`, hors de l'allowlist de
  l'authorizer d'exécution. L'introspection (`get_schema`, correspondance mois/millésime)
  utilise donc une connexion sans authorizer, qui n'exécute que du SQL écrit par nous.
- **Authorizer en allowlist** (`SQLITE_SELECT` + `SQLITE_READ` + `SQLITE_FUNCTION`, deny
  par défaut), déterminée en instrumentant dix formes de requêtes légitimes. Une liste
  noire de colonnes sensibles seule laissait passer les `UPDATE` — vérifié avant correction.
- **Fuite de schéma par `sqlite_master` corrigée** : sans règle dédiée, `SELECT name, sql
  FROM sqlite_master` retournait le `CREATE TABLE` complet au profil `support`, révélant
  l'existence des colonnes sensibles malgré leur filtrage dans `get_schema`. Toute lecture
  de table `sqlite_%` est désormais refusée par l'authorizer.
- **Mois sans année résolu côté code, pas deviné par le modèle** : « combien de commandes
  en avril ? » est ambigu sans contexte de dates additionnel, mais le comportement du
  modèle sans ce contexte s'est avéré instable (devinette silencieuse ou refus au
  raisonnement faux selon le prompt). Le code calcule désormais, par introspection des
  données, le millésime de chaque mois présent (avril -> 2026, octobre -> 2025) et
  l'injecte comme un fait dans le prompt de génération.
- **Détection d'intention d'écriture avant l'appel LLM** (`code="FORBIDDEN"`), dupliquée
  dans un fichier d'alerte dédié (`logs/tentatives_ecriture.jsonl`) en plus du journal
  unique — décision prise après relecture manuelle des points ouverts de la spec.
- **`tests/acceptance/test_sql.py` et `tests/conftest.py`, lus mais pas modifiés** :
  `conftest.py::TOOLS_BY_PROFILE` masque `get_schema` au profil `support`, ce qui reprend
  la matrice de `docs/cadrage_dsi.md` déjà écartée — la conception (get_schema appelable
  par les deux profils, contenu filtré) fait autorité ; `SqlEngine.get_schema()` suit donc
  la conception, et l'alignement de `conftest.py` reste au Chantier 3.
- Mesure sur `eval/questions_sql.jsonl` (`make eval-sql`, `eval/rapport_sql.md`) :
  **24/24 questions conformes** — 12 `metier` (10 exécutées, 2 en clarification légitime
  sur une période non précisée), 4 `ecriture` refusées avant tout appel LLM, 4
  `table_interdite` refusées, 2 `hors_schema` refusées, 2 `ambigue` en clarification.

## 2026-09-02 — Retrieval hybride + rerank opérationnel, agent de démo

- `retrieval/` construit selon `docs/spec_retrieval.md` et `docs/plan_retrieval.md` (11
  tâches, TDD) : recherche dense (Chroma), BM25 en mémoire, fusion RRF (k=60), filtrage
  de version + diversification, rerank Cohere (Azure AI Foundry), routing des références
  exactes, décision de refus hors-corpus (E1), rédaction de réponse sourcée (`gpt-5.4-mini`).
- Extraction de `gateway/` (settings, embedder, accès Chroma) partagé entre `ingest/` et
  `retrieval/` — évite que `retrieval/` importe depuis `ingest/`.
- Agent CLI `scripts/demo_agent.py` (`make demo`) : `--show-stages` détaille chaque étape
  du pipeline, `--no-rerank` montre l'apport du reranking en désactivant le seuil de refus.
- **API de rerank découverte par vérification directe** (non documentée par Azure AI
  Foundry dans le SDK openai) : `POST {endpoint sans /openai/v1}/models/v1/rerank`,
  en-tête `api-key`, format Cohere v1 — 14 routes testées avant de trouver la bonne.
  Schéma dédié : `docs/schemas/rerank_reel.drawio`.
- **Calibration du seuil de refus (E1)**, `scripts/eval_rag.py` (`make eval`) sur les
  30 questions de `eval/questions_rag.jsonl` : seuil retenu **0.65** (max hors-corpus
  mesuré 0.626, min couvertes mesuré 0.669 — séparation parfaite entre 0.64 et 0.66).
  La valeur provisoire de 0.40 posée dans la spec était trop basse — trouvé en testant
  l'agent en conditions réelles avant la calibration (question hors-corpus à 0.4164,
  juste au-dessus, donc non refusée par le moteur).
- Mesure E6 (`eval/rapport_gain.md`) : couvertes top-5 — dense seul 13/14, hybride
  12/14, hybride+rerank 13/14 ; références exactes 8/8 dans les trois configurations
  (routing déterministe, ne dépend pas du classement).
- Couverture : 73 tests (69 unitaires + 4 d'intégration sur les 400 vrais chunks,
  Chroma éphémère + embedder/reranker factices — ni Docker ni réseau requis en CI).
  `ruff` et `mypy` propres.
- Reste ouvert : `tests/acceptance/` encode toujours le contrat de `docs/cadrage_dsi.md`
  (retiré) — à trancher avec le formateur avant le chantier MCP. Aucun tool MCP construit
  ici : l'agent appelle le moteur directement, en Python.

## 2026-09-01 — Ingestion du corpus opérationnelle

- `ingest/` construit selon `docs/spec_ingestion.md` et `docs/plan_ingestion.md` (11
  tâches, TDD) : extraction PDF (`pypdf`)/HTML/Markdown (`beautifulsoup4`/`markdown`),
  dérivation des métadonnées (`family_id`, `diversification_group`), chunking (1 chunk
  = 1 document), embeddings Azure, écriture Chroma.
- Point d'entrée `scripts/run_ingest.py` + cible `make ingest`.
- **Bug trouvé et corrigé en cours de route** : le plan nommait le script
  `scripts/ingest.py` — en l'exécutant directement, `scripts/` passe en tête de
  `sys.path` et masque le paquet `ingest/` du même nom (`ModuleNotFoundError:
  'ingest' is not a package`). Aucun test ne l'avait détecté (aucun ne lance le
  script comme processus séparé). Renommé `run_ingest.py`.
- Constaté sur le vrai serveur Chroma (`make up`) avec les vraies clés Azure :
  **400 chunks** dans `sorabel_corpus`, vecteurs de dimension **1536**
  (`text-embedding-3-small`), ingestion complète en **~11-13 s**.
- Idempotence vérifiée sur le vrai serveur : seconde exécution de `make ingest`,
  compte inchangé à 400.
- Couverture : 33 tests (28 unitaires + 5 d'intégration sur les 400 vrais fichiers,
  Chroma éphémère + embedder factice — ni Docker ni réseau requis en CI). `ruff` et
  `mypy` propres (ajout de `types-Markdown` en dépendance dev, absent du plan
  initial).
- Reste ouvert : `tests/acceptance/` et `tests/conftest.py` encodent le contrat de
  `docs/cadrage_dsi.md` (document retiré par le formateur) — à trancher avec lui
  avant le chantier MCP.

## 2026-09-01 — Bascule en implémentation : branches, embeddings Azure, premiers modèles ingest

- **Branches** : passage de `main` seul à `main` + `dev`. `.github/workflows/quality.yml`
  tourne désormais aussi sur push vers `dev` (pas seulement `main`/PR). Pas de
  `cd-staging.yml`/`cd-main.yml` — aucune cible de déploiement réelle pour ce projet,
  jugé disproportionné (voir échange de conception).
- **`docs/cadrage_dsi.md` écarté** : oubli de suppression du scaffold, confirmé par le
  formateur (Elbby Skermine, 2026-09-01). Références retirées de `README.md`
  (« Contrat d'intégration », layout). Aucune décision de conception modifiée sur sa
  base — voir le `CHANGELOG.md` racine pour le détail des contradictions constatées.
- **Embeddings : Azure AI Foundry plutôt que local** — `.env` réel montrait
  `AZURE_MODEL_TEXT_EMBEDDING_SMALL=text-embedding-3-small` via un endpoint
  `/openai/v1` compatible OpenAI, pas de modèle local. Retiré `sentence-transformers`
  (extra `vector`) de `pyproject.toml`, ajouté `openai>=1.50,<2`. Raison initiale :
  `sentence-transformers` embarque `torch`, dont le wheel PyPI par défaut tire les
  paquets CUDA (`nvidia-*`) même sans GPU — plusieurs Go pour rien en local.
  `.venv` : 390 Mo sans torch. `.env.example` et `README.md` (section Stack) mis à jour
  en cohérence.
- **Reranking : `Cohere-rerank-v4.0-pro` via Azure AI Foundry** — même ressource Azure
  que les embeddings, pas de nouveau fournisseur/clé. `fast` non déployable sur la
  ressource (contrainte de disponibilité, pas un choix qualité). `.env.example` mis à
  jour (`AZURE_MODEL_RERANKING`).
- **Ajout `markdown>=3.6,<4`** pour normaliser le corpus `notes/` (Markdown) — même
  pipeline d'extraction que le HTML (`sav/`) : Markdown → HTML (`markdown`) → texte
  brut (`beautifulsoup4`, déjà en dépendance), un seul chemin de code pour les deux
  formats plutôt que deux parseurs séparés.
- **`ingest/document.py` (`DocumentCanonique`) et `ingest/chunk.py` (`Chunk`)** : modèles
  Pydantic portés depuis `conception/1_RAG/modele_document_canonique.py` et
  `modele_chunk.py`, sans modification de structure — vérifiés par import et
  instanciation réelle. Les fichiers de conception d'origine restent inchangés,
  référencés en tête de chaque module porté.
