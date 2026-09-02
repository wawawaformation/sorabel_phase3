# Changelog — implémentation

Tâches réalisées, plus récentes en premier. Décisions de conception : voir `conception/` et son propre `CHANGELOG.md` (racine du projet).

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
