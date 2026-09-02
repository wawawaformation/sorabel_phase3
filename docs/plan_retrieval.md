# Plan d'implémentation — Retrieval hybride, rerank et agent de démo

> **Pour un worker agentique :** SOUS-SKILL REQUIS : utiliser `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans` pour dérouler ce plan tâche par tâche. Les étapes sont en cases à cocher (`- [ ]`).

**Objectif :** construire le retrieval hybride (Dense + BM25 + RRF + rerank Cohere, avec refus hors corpus) et un agent CLI qui l'interroge, pour une démonstration du RAG hybride avec reranking.

**Architecture :** un module par étape du pipeline dans `retrieval/`, orchestrés par `retrieval/engine.py`. Chaque classement intermédiaire est une **liste de `chunk_id` ordonnée** — c'est ce qui permet de fusionner par RRF (qui travaille sur des rangs) et de tester chaque étape isolément. Les dépendances externes (Chroma, embedder, reranker, LLM) sont **injectées**, jamais construites dans le pipeline.

**Stack :** Python 3.11, `chromadb`, `rank-bm25`, `httpx` (rerank), SDK `openai` (embeddings + génération), `pydantic-settings`, `pytest`, `ruff`, `mypy`.

**Spec de référence :** `docs/spec_retrieval.md`. Étape précédente : `docs/spec_ingestion.md`.

## Contraintes globales

- Ligne max **100** caractères (`ruff`), cible `py311`. `mypy` doit passer sur `retrieval` et `gateway`.
- **Aucun test ne doit exiger le réseau ni Docker** (la CI n'a ni clé Azure ni `docker compose`) : embedder, reranker et client LLM sont injectés et remplacés par des doubles dans les tests. Exception explicite : la Task 7 comporte une vérification manuelle contre le vrai endpoint, hors suite de tests.
- Code en anglais, commentaires en français.
- Les modèles `ingest/document.py` et `ingest/chunk.py` ne sont **pas** modifiés.
- L'ingestion doit avoir été lancée (`make up && make ingest`) pour que Chroma contienne les 400 chunks.

### API de rerank (vérifiée, § 2.1 de la spec)

```text
POST {AZURE_AI_ENDPOINT sans "/openai/v1"}/models/v1/rerank
en-têtes : Content-Type: application/json | api-key: <AZURE_AI_API_KEY>
corps    : {"model": <AZURE_MODEL_RERANKING>, "query": str,
            "documents": [str, …], "top_n": int}
réponse  : {"results": [{"index": int, "relevance_score": float}, …], …}
           index = position dans "documents" ; triés par score décroissant ; score absolu [0,1]
```

Le SDK `openai` **ne sait pas** appeler cette route (ce n'est pas une opération OpenAI).

### Volumes du pipeline (spec § 4.2)

| Étape | Volume |
|---|---|
| Candidats Dense | 30 |
| Candidats BM25 | 30 |
| Après fusion RRF (k=60) | 20 |
| Envoyés au reranker | 10 |
| Retournés (`top_k`) | 5 |

### Priorités si le temps manque

Tasks 1 à 10 = la démonstration. Task 11 (calibration + rapport E6) peut atterrir après la démo ; dans ce cas le seuil de refus reste à sa valeur provisoire de 0,40.

---

## Structure de fichiers

| Fichier | Responsabilité |
|---|---|
| `gateway/settings.py` | configuration partagée (déplacée depuis `ingest/settings.py`, étendue) |
| `retrieval/tokenize.py` | tokenisation FR pour BM25 |
| `retrieval/corpus.py` | chargement des chunks depuis Chroma → `IndexedChunk` |
| `retrieval/dense.py` | recherche dense (Chroma + embedding) → classement |
| `retrieval/lexical.py` | index BM25 en mémoire → classement |
| `retrieval/fusion.py` | fusion RRF de plusieurs classements |
| `retrieval/dedup.py` | dernière version par `family_id`, diversification par `diversification_group` |
| `retrieval/reranker.py` | protocole `Reranker` + implémentation Azure/Cohere (HTTP) |
| `retrieval/routing.py` | détection `REF-nnnn` + lookup par métadonnée |
| `retrieval/engine.py` | orchestration complète + décision de refus |
| `retrieval/answer.py` | rédaction de la réponse sourcée (`gpt-5.4-mini`) |
| `scripts/demo_agent.py` | agent CLI de démonstration |
| `scripts/eval_rag.py` | calibration du seuil + rapport E6 |
| `tests/unit/…`, `tests/integration/…` | tests |

---

## Task 1 : Extraire les briques partagées dans `gateway/`

`ingest/settings.py` portait la mention « à déplacer dans un module partagé quand retrieval/ en aura besoin ». C'est le moment, et le besoin dépasse les settings : le retrieval a aussi besoin de l'embedder (pour vectoriser la question) et du client Chroma. Ces trois briques sont de l'infrastructure partagée — `retrieval/` ne doit pas importer depuis `ingest/`, la dépendance irait dans le mauvais sens.

Ce qui reste **spécifique** à l'ingestion ne bouge pas : `embedding_text(chunk)` (dépend de `Chunk`), `chroma_metadata(chunk)` et `upsert_chunks` (écriture).

**Files:**
- Create: `gateway/__init__.py` (vide), `gateway/settings.py`, `gateway/embedder.py`, `gateway/chroma.py`
- Delete: `ingest/settings.py`
- Modify: `ingest/embedder.py` (ne garde que `embedding_text`), `ingest/store.py` (ne garde que `chroma_metadata` et `upsert_chunks`), `ingest/pipeline.py`, `scripts/run_ingest.py`, `pyproject.toml` (liste `include`), `tests/unit/test_settings.py`, `tests/unit/test_embedder.py`, `tests/unit/test_store.py`, `tests/integration/test_ingestion.py` (imports)
- Test: `tests/unit/test_settings.py`

**Interfaces:**
- Produces: `gateway.settings.Settings` avec, en plus des champs existants (`corpus_dir`, `chroma_url`, `chroma_collection`, `azure_ai_endpoint`, `azure_ai_api_key`, `azure_model_text_embedding_small`) : `azure_model_reranking: str`, `azure_model_text_generation: str`, `rerank_enabled: bool`, `refusal_threshold: float`, `dense_candidates: int`, `lexical_candidates: int`, `fusion_candidates: int`, `rerank_candidates: int`, `top_k: int`, `rrf_k: int`, et la propriété `azure_models_base_url: str`. `gateway.settings.get_settings()`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_settings.py — remplacer entièrement le fichier
from pathlib import Path

from gateway.settings import Settings


def test_valeurs_par_defaut():
    s = Settings(_env_file=None)
    assert s.corpus_dir == Path("data/corpus")
    assert s.chroma_collection == "sorabel_corpus"
    assert s.azure_model_text_embedding_small == "text-embedding-3-small"
    # nouveaux champs du retrieval
    assert s.rerank_enabled is True
    assert s.refusal_threshold == 0.40
    assert (s.dense_candidates, s.lexical_candidates) == (30, 30)
    assert (s.fusion_candidates, s.rerank_candidates, s.top_k) == (20, 10, 5)
    assert s.rrf_k == 60


def test_base_url_des_modeles_non_openai():
    # Le rerank vit sous /models, pas sous /openai/v1 : la propriété retire ce suffixe.
    s = Settings(_env_file=None, azure_ai_endpoint="https://x.services.ai.azure.com/openai/v1")
    assert s.azure_models_base_url == "https://x.services.ai.azure.com"


def test_lecture_depuis_environnement(monkeypatch):
    monkeypatch.setenv("AZURE_AI_API_KEY", "cle-de-test")
    monkeypatch.setenv("RERANK_ENABLED", "false")
    s = Settings(_env_file=None)
    assert s.azure_ai_api_key == "cle-de-test"
    assert s.rerank_enabled is False


def test_variables_inconnues_ignorees(monkeypatch):
    monkeypatch.setenv("SORABEL_PROFILE", "support")
    monkeypatch.setenv("GATEWAY_JOURNAL", "logs/journal.jsonl")
    Settings(_env_file=None)
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_settings.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'gateway'`

- [ ] **Step 3 : écrire l'implémentation**

```python
# gateway/settings.py
"""Configuration partagée par l'ingestion, le retrieval et (plus tard) le serveur MCP."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" : .env porte aussi des variables d'autres modules
    # (SORABEL_PROFILE, GATEWAY_JOURNAL…).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Corpus & index ---
    corpus_dir: Path = Path("data/corpus")
    chroma_url: str = "http://localhost:8002"
    chroma_collection: str = "sorabel_corpus"

    # --- Azure AI Foundry ---
    azure_ai_endpoint: str = ""
    azure_ai_api_key: str = ""
    azure_model_text_embedding_small: str = "text-embedding-3-small"
    azure_model_reranking: str = "Cohere-rerank-v4.0-pro"
    azure_model_text_generation: str = "gpt-5.4-mini"

    # --- Retrieval ---
    rerank_enabled: bool = True
    refusal_threshold: float = 0.40  # valeur provisoire, calibrée par scripts/eval_rag.py
    dense_candidates: int = 30
    lexical_candidates: int = 30
    fusion_candidates: int = 20
    rerank_candidates: int = 10
    top_k: int = 5
    rrf_k: int = 60

    @property
    def azure_models_base_url(self) -> str:
        """Base des modèles non-OpenAI (rerank) : l'endpoint sans le suffixe /openai/v1."""
        return self.azure_ai_endpoint.removesuffix("/openai/v1").rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Créer `gateway/__init__.py` vide, supprimer `ingest/settings.py`, et dans `pyproject.toml` :

```toml
include = ["gateway*", "ingest*", "retrieval*", "sql*", "mcp_server*"]
```

- [ ] **Step 4 : déplacer l'embedder partagé**

`gateway/embedder.py` reçoit le protocole et l'implémentation Azure — déplacés **sans
modification** depuis `ingest/embedder.py` (seul l'import des settings change) :

```python
# gateway/embedder.py
"""Calcul des embeddings via Azure AI Foundry (endpoint compatible OpenAI).

Partagé : l'ingestion vectorise les documents, le retrieval vectorise la question.
Le protocole Embedder existe pour que les pipelines soient testables sans réseau ni
clé d'API — les tests injectent un embedder factice.
"""

from typing import Protocol

from openai import OpenAI

from gateway.settings import Settings

BATCH_SIZE = 64


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class AzureEmbedder:
    """Embedder Azure. La dimension du vecteur est celle que renvoie le modèle."""

    def __init__(self, settings: Settings) -> None:
        self._client = OpenAI(
            base_url=settings.azure_ai_endpoint,
            api_key=settings.azure_ai_api_key,
        )
        self._model = settings.azure_model_text_embedding_small

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in response.data]


def embed_in_batches(
    embedder: Embedder, texts: list[str], batch_size: int = BATCH_SIZE
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(embedder.embed(texts[start : start + batch_size]))
    return vectors
```

`ingest/embedder.py` ne garde que ce qui est propre à l'ingestion (`embedding_text` dépend
de `Chunk`) :

```python
# ingest/embedder.py
"""Texte servant au vecteur dense d'un chunk. L'embedder lui-même est dans gateway/."""

from ingest.chunk import Chunk


def embedding_text(chunk: Chunk) -> str:
    """Texte servant au vecteur dense : title + ref_produit + content.

    Aide le matching sémantique quand la requête ne contient pas de REF-xxxx
    explicite. Le content stocké et retourné, lui, n'est jamais modifié.
    """
    parts = [chunk.title]
    if chunk.ref_produit:
        parts.append(chunk.ref_produit)
    parts.append(chunk.content)
    return " ".join(parts)
```

- [ ] **Step 5 : déplacer les helpers Chroma partagés**

`gateway/chroma.py` reçoit `chroma_client` et `open_collection`, déplacés **sans
modification** depuis `ingest/store.py` (ouvrir une collection sert aussi au retrieval) :

```python
# gateway/chroma.py
"""Accès au serveur Chroma, partagé par l'ingestion et le retrieval."""

from urllib.parse import urlparse

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from gateway.settings import Settings


def chroma_client(settings: Settings) -> ClientAPI:
    parsed = urlparse(settings.chroma_url)
    return chromadb.HttpClient(
        host=parsed.hostname or "localhost", port=parsed.port or 8000
    )


def open_collection(client: ClientAPI, name: str) -> Collection:
    """Ouvre ou crée la collection, sans fonction d'embedding.

    Aucune embedding_function n'est passée : les vecteurs sont toujours fournis
    explicitement. Laisser Chroma calculer déclencherait le téléchargement de son
    modèle ONNX par défaut.
    """
    return client.get_or_create_collection(name=name, embedding_function=None)
```

`ingest/store.py` ne garde que l'écriture (`chroma_metadata`, `upsert_chunks`) : retirer
`chroma_client`, `open_collection`, et les imports devenus inutiles (`urlparse`, `chromadb`,
`ClientAPI`, `Settings`).

- [ ] **Step 6 : mettre à jour tous les imports**

| Fichier | Remplacer par |
|---|---|
| `ingest/pipeline.py` | `from gateway.embedder import Embedder, embed_in_batches` + `from ingest.embedder import embedding_text` |
| `scripts/run_ingest.py` | `from gateway.embedder import AzureEmbedder` + `from gateway.chroma import chroma_client, open_collection` + `from gateway.settings import get_settings` |
| `tests/unit/test_embedder.py` | `from gateway.embedder import embed_in_batches` + `from ingest.embedder import embedding_text` |
| `tests/unit/test_store.py` | `from gateway.chroma import open_collection` + `from ingest.store import chroma_metadata, upsert_chunks` |
| `tests/integration/test_ingestion.py` | `from gateway.chroma import open_collection` |

- [ ] **Step 7 : lancer toute la suite, vérifier qu'elle passe**

Run : `uv run pytest tests/unit tests/integration -q && uv run ruff check . && uv run mypy gateway ingest`
Attendu : 34 tests PASS (33 d'ingestion + le test de `azure_models_base_url`), lint et mypy OK.
Un test rouge ici signale un import oublié : le déplacement est purement mécanique, rien
d'autre n'a changé.

- [ ] **Step 8 : commit**

```bash
git add gateway pyproject.toml ingest scripts/run_ingest.py tests
git commit -m "refactor: extract shared settings, embedder and Chroma access into gateway/"
```

---

## Task 2 : Tokenisation et chargement du corpus

**Files:**
- Create: `retrieval/tokenize.py`, `retrieval/corpus.py`
- Test: `tests/unit/test_corpus.py`

**Interfaces:**
- Produces: `tokenize(text: str) -> list[str]` ; `IndexedChunk` (dataclass gelée : `chunk_id`, `document_id`, `content`, `title`, `type_doc`, `collection`, `version`, `date`, `source`, `family_id`, `diversification_group`, `ref_produit: str | None`) ; `load_chunks(collection) -> list[IndexedChunk]` ; `by_chunk_id(chunks) -> dict[str, IndexedChunk]`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_corpus.py
import chromadb

from retrieval.corpus import IndexedChunk, by_chunk_id, load_chunks
from retrieval.tokenize import tokenize


def test_tokenisation_replie_accents_et_decoupe():
    assert tokenize("Disjoncteur triphasé 63 A — REF-8842, 230/400 V") == [
        "disjoncteur", "triphase", "63", "a", "ref", "8842", "230", "400", "v",
    ]


def test_chargement_depuis_chroma():
    client = chromadb.EphemeralClient()
    col = client.get_or_create_collection(name="corpus_test", embedding_function=None)
    col.upsert(
        ids=["a#0", "b#0"],
        embeddings=[[0.1, 0.2], [0.3, 0.4]],
        documents=["contenu a", "contenu b"],
        metadatas=[
            {"document_id": "a", "chunk_index": 0, "family_id": "fam-a",
             "diversification_group": "grp-a", "title": "Titre A",
             "type_doc": "fiche_technique", "collection": "fiches", "version": "2.1",
             "date": "2022-11-07", "source": "pdf", "ref_produit": "REF-1024"},
            # pas de ref_produit : cas normal de sav/ et notes/
            {"document_id": "b", "chunk_index": 0, "family_id": "fam-b",
             "diversification_group": "grp-b", "title": "Titre B",
             "type_doc": "procedure_sav", "collection": "sav", "version": "1.0",
             "date": "2026-04-05", "source": "html"},
        ],
    )
    chunks = load_chunks(col)
    assert len(chunks) == 2
    index = by_chunk_id(chunks)
    assert index["a#0"].ref_produit == "REF-1024"
    assert index["b#0"].ref_produit is None  # clé absente → None
    assert index["a#0"].content == "contenu a"
    assert isinstance(index["a#0"], IndexedChunk)
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_corpus.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'retrieval.corpus'`

- [ ] **Step 3 : écrire l'implémentation**

```python
# retrieval/tokenize.py
"""Tokenisation pour BM25.

Repli des diacritiques : le corpus est en français, « triphasé » et « triphase » doivent
donner le même jeton. Vérifié sur le corpus réel (spec § 2.5).
"""

import re
import unicodedata

RE_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return RE_TOKEN.findall(folded)
```

```python
# retrieval/corpus.py
"""Chargement des chunks indexés depuis Chroma.

Les 400 chunks tiennent en mémoire : l'index BM25 se reconstruit au démarrage, rien
n'est persisté (spec § 4.6).
"""

from dataclasses import dataclass

from chromadb.api.models.Collection import Collection


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    document_id: str
    content: str
    title: str
    type_doc: str
    collection: str
    version: str
    date: str  # ISO, tel que stocké dans Chroma
    source: str
    family_id: str
    diversification_group: str
    ref_produit: str | None


def load_chunks(collection: Collection) -> list[IndexedChunk]:
    got = collection.get(include=["documents", "metadatas"])
    chunks: list[IndexedChunk] = []
    for chunk_id, content, meta in zip(
        got["ids"], got["documents"] or [], got["metadatas"] or [], strict=True
    ):
        chunks.append(
            IndexedChunk(
                chunk_id=chunk_id,
                document_id=str(meta["document_id"]),
                content=content,
                title=str(meta["title"]),
                type_doc=str(meta["type_doc"]),
                collection=str(meta["collection"]),
                version=str(meta["version"]),
                date=str(meta["date"]),
                source=str(meta["source"]),
                family_id=str(meta["family_id"]),
                diversification_group=str(meta["diversification_group"]),
                # clé absente pour sav/ et notes/ : Chroma refuse les valeurs None
                ref_produit=str(meta["ref_produit"]) if "ref_produit" in meta else None,
            )
        )
    return chunks


def by_chunk_id(chunks: list[IndexedChunk]) -> dict[str, IndexedChunk]:
    return {c.chunk_id: c for c in chunks}
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_corpus.py -v`
Attendu : 2 PASS

- [ ] **Step 5 : commit**

```bash
git add retrieval/tokenize.py retrieval/corpus.py tests/unit/test_corpus.py
git commit -m "feat(retrieval): French tokenizer and Chroma chunk loading"
```

---

## Task 3 : Recherche dense

**Files:**
- Create: `retrieval/dense.py`
- Test: `tests/unit/test_dense.py`

**Interfaces:**
- Consumes: `gateway.embedder.Embedder` (protocole déplacé en Task 1, méthode `embed(texts) -> list[list[float]]`).
- Produces: `dense_search(collection, embedder, query: str, limit: int) -> list[str]` (chunk_ids, du plus proche au plus lointain).

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_dense.py
import chromadb

from retrieval.dense import dense_search


class FakeEmbedder:
    """Renvoie un vecteur fixe : c'est Chroma qui fait le classement, pas l'embedder."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def _collection():
    client = chromadb.EphemeralClient()
    col = client.get_or_create_collection(name="dense_test", embedding_function=None)
    col.upsert(
        ids=["proche#0", "loin#0"],
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
        documents=["proche", "loin"],
        metadatas=[{"collection": "fiches"}, {"collection": "fiches"}],
    )
    return col


def test_classement_par_proximite():
    ids = dense_search(_collection(), FakeEmbedder(), "peu importe", limit=2)
    assert ids == ["proche#0", "loin#0"]


def test_limite_respectee():
    assert dense_search(_collection(), FakeEmbedder(), "q", limit=1) == ["proche#0"]
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_dense.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'retrieval.dense'`

- [ ] **Step 3 : écrire l'implémentation**

```python
# retrieval/dense.py
"""Recherche dense : embedding de la question, puis plus proches voisins dans Chroma."""

from chromadb.api.models.Collection import Collection

from gateway.embedder import Embedder


def dense_search(
    collection: Collection, embedder: Embedder, query: str, limit: int
) -> list[str]:
    """Retourne les chunk_id classés du plus proche au plus lointain."""
    vector = embedder.embed([query])[0]
    result = collection.query(query_embeddings=[vector], n_results=limit)
    ids = result["ids"]
    return list(ids[0]) if ids else []
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_dense.py -v`
Attendu : 2 PASS

- [ ] **Step 5 : commit**

```bash
git add retrieval/dense.py tests/unit/test_dense.py
git commit -m "feat(retrieval): dense search over Chroma"
```

---

## Task 4 : Recherche lexicale BM25

**Files:**
- Create: `retrieval/lexical.py`
- Test: `tests/unit/test_lexical.py`

**Interfaces:**
- Consumes: `IndexedChunk` (Task 2), `tokenize` (Task 2).
- Produces: `LexicalIndex(chunks: list[IndexedChunk])` avec `search(query: str, limit: int) -> list[str]`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_lexical.py
from retrieval.corpus import IndexedChunk
from retrieval.lexical import LexicalIndex


def _chunk(chunk_id: str, content: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=chunk_id, document_id=chunk_id.split("#")[0], content=content,
        title="T", type_doc="fiche_technique", collection="fiches", version="1.0",
        date="2024-01-01", source="pdf", family_id="fam", diversification_group="grp",
        ref_produit=None,
    )


def _index() -> LexicalIndex:
    return LexicalIndex([
        _chunk("colis#0", "Procédure SAV : colis reçu endommagé, constat et photos."),
        _chunk("led#0", "Notice d'installation projecteur LED rechargeable."),
        _chunk("disj#0", "Fiche technique disjoncteur triphasé 63 A courbe D."),
    ])


def test_match_lexical_classe_en_tete():
    assert _index().search("colis endommagé", limit=3)[0] == "colis#0"


def test_accents_indifferents():
    # « triphase » sans accent doit retrouver « triphasé » (tokenisation repliée).
    assert _index().search("disjoncteur triphase", limit=1) == ["disj#0"]


def test_limite_respectee():
    assert len(_index().search("colis", limit=2)) == 2


def test_index_vide_ne_plante_pas():
    assert LexicalIndex([]).search("colis", limit=5) == []
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_lexical.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'retrieval.lexical'`

- [ ] **Step 3 : écrire l'implémentation**

```python
# retrieval/lexical.py
"""Recherche lexicale BM25, index en mémoire reconstruit au démarrage."""

from rank_bm25 import BM25Okapi

from retrieval.corpus import IndexedChunk
from retrieval.tokenize import tokenize


class LexicalIndex:
    def __init__(self, chunks: list[IndexedChunk]) -> None:
        self._chunk_ids = [c.chunk_id for c in chunks]
        corpus = [tokenize(c.content) for c in chunks]
        # BM25Okapi refuse un corpus vide : on garde l'index inactif dans ce cas.
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, limit: int) -> list[str]:
        """Retourne les chunk_id classés par score BM25 décroissant."""
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self._chunk_ids[i] for i in ranked[:limit]]
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_lexical.py -v`
Attendu : 4 PASS

- [ ] **Step 5 : commit**

```bash
git add retrieval/lexical.py tests/unit/test_lexical.py
git commit -m "feat(retrieval): in-memory BM25 lexical index"
```

---

## Task 5 : Fusion RRF

**Files:**
- Create: `retrieval/fusion.py`
- Test: `tests/unit/test_fusion.py`

**Interfaces:**
- Produces: `reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60, limit: int | None = None) -> list[str]`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_fusion.py
from retrieval.fusion import reciprocal_rank_fusion


def test_document_present_dans_les_deux_classements_remonte():
    # « b » est 2e partout ; « a » est 1er dans A mais absent de B.
    # Somme RRF (k=60) : b = 1/61 + 1/61 ≈ 0.0328 > a = 1/61 ≈ 0.0164
    assert reciprocal_rank_fusion([["a", "b"], ["c", "b"]])[0] == "b"


def test_rangs_pris_en_compte_pas_les_scores():
    # Aucun score n'est fourni : seule la position compte (c'est l'intérêt de RRF,
    # les scores vectoriels et BM25 n'étant pas sur la même échelle).
    assert reciprocal_rank_fusion([["a", "b", "c"]]) == ["a", "b", "c"]


def test_limite_respectee():
    assert reciprocal_rank_fusion([["a", "b", "c"]], limit=2) == ["a", "b"]


def test_classements_vides():
    assert reciprocal_rank_fusion([[], []]) == []


def test_k_modifie_l_ecrasement_des_rangs():
    # Avec un k très petit, le 1er rang pèse beaucoup plus lourd : « a », premier
    # d'un seul classement, dépasse « b » qui est 2e dans les deux.
    assert reciprocal_rank_fusion([["a", "b"], ["c", "b"]], k=1)[0] == "a"
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_fusion.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'retrieval.fusion'`

- [ ] **Step 3 : écrire l'implémentation**

```python
# retrieval/fusion.py
"""Reciprocal Rank Fusion.

RRF fusionne des classements par la position des documents, pas par leur score : un
score vectoriel et un score BM25 ne sont pas sur la même échelle et ne se comparent pas
directement (conception § « Recherche hybride »).
"""

from collections import defaultdict

RRF_K = 60  # constante de l'article d'origine


def reciprocal_rank_fusion(
    rankings: list[list[str]], k: int = RRF_K, limit: int | None = None
) -> list[str]:
    """Fusionne des classements de chunk_id ; retourne le classement hybride."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1.0 / (k + rank)
    fused = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return fused[:limit] if limit is not None else fused
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_fusion.py -v`
Attendu : 5 PASS

- [ ] **Step 5 : commit**

```bash
git add retrieval/fusion.py tests/unit/test_fusion.py
git commit -m "feat(retrieval): reciprocal rank fusion"
```

---

## Task 6 : Versions et diversification

Mesuré sur le corpus réel : le top-3 BM25 d'une question SAV renvoie **le même titre trois fois** (spec § 2.3). Deux filtres distincts sont nécessaires.

**Files:**
- Create: `retrieval/dedup.py`
- Test: `tests/unit/test_dedup.py`

**Interfaces:**
- Consumes: `IndexedChunk` (Task 2).
- Produces: `keep_latest_version(chunk_ids, by_id) -> list[str]`, `diversify(chunk_ids, by_id) -> list[str]`, `version_key(version: str) -> tuple[int, ...]`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_dedup.py
from retrieval.corpus import IndexedChunk
from retrieval.dedup import diversify, keep_latest_version, version_key


def _chunk(chunk_id: str, family: str, version: str, group: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=chunk_id, document_id=chunk_id.split("#")[0], content="c", title="T",
        type_doc="procedure_sav", collection="sav", version=version, date="2024-01-01",
        source="html", family_id=family, diversification_group=group, ref_produit=None,
    )


BY_ID = {
    c.chunk_id: c
    for c in [
        _chunk("proc-01-v1.0#0", "proc-01", "1.0", "sav_casse"),
        _chunk("proc-01-v2.0#0", "proc-01", "2.0", "sav_casse"),
        _chunk("proc-02-v1.0#0", "proc-02", "1.0", "sav_casse"),
        _chunk("notice-v1.1#0", "notice", "1.1", "notice_x"),
    ]
}


def test_version_key_compare_numeriquement():
    assert version_key("2.1") == (2, 1)
    assert version_key("1.10") > version_key("1.9")  # 10 > 9, pas un tri de chaînes


def test_garde_la_derniere_version_de_chaque_famille():
    # v1.0 est mieux classée, mais c'est la v2.0 qui doit sortir : la conception
    # retient « dernière version par défaut ».
    got = keep_latest_version(["proc-01-v1.0#0", "proc-01-v2.0#0", "notice-v1.1#0"], BY_ID)
    assert got == ["proc-01-v2.0#0", "notice-v1.1#0"]


def test_position_de_la_famille_preservee():
    # La famille garde le meilleur rang qu'elle occupait.
    got = keep_latest_version(["notice-v1.1#0", "proc-01-v1.0#0", "proc-01-v2.0#0"], BY_ID)
    assert got == ["notice-v1.1#0", "proc-01-v2.0#0"]


def test_diversification_un_seul_par_groupe():
    got = diversify(["proc-01-v2.0#0", "proc-02-v1.0#0", "notice-v1.1#0"], BY_ID)
    assert got == ["proc-01-v2.0#0", "notice-v1.1#0"]


def test_listes_vides():
    assert keep_latest_version([], BY_ID) == []
    assert diversify([], BY_ID) == []
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_dedup.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'retrieval.dedup'`

- [ ] **Step 3 : écrire l'implémentation**

```python
# retrieval/dedup.py
"""Deux filtres distincts, mesurés nécessaires sur le corpus réel (spec § 2.3).

- keep_latest_version : plusieurs versions d'un même document logique (family_id) —
  on retourne la plus récente, pas la mieux classée.
- diversify : plusieurs documents voisins d'un même thème (diversification_group) —
  on n'en garde qu'un, pour ne pas remplir le top-k de quasi-doublons.
"""

from retrieval.corpus import IndexedChunk


def version_key(version: str) -> tuple[int, ...]:
    """« 2.1 » → (2, 1). Comparaison numérique : « 1.10 » est postérieure à « 1.9 »."""
    parts = []
    for part in version.split("."):
        parts.append(int(part) if part.isdigit() else 0)
    return tuple(parts)


def keep_latest_version(
    chunk_ids: list[str], by_id: dict[str, IndexedChunk]
) -> list[str]:
    """Une seule entrée par family_id : la version la plus récente, au meilleur rang."""
    best: dict[str, str] = {}
    order: list[str] = []
    for chunk_id in chunk_ids:
        family = by_id[chunk_id].family_id
        if family not in best:
            best[family] = chunk_id
            order.append(family)
            continue
        current = by_id[best[family]]
        if version_key(by_id[chunk_id].version) > version_key(current.version):
            best[family] = chunk_id
    return [best[family] for family in order]


def diversify(chunk_ids: list[str], by_id: dict[str, IndexedChunk]) -> list[str]:
    """Un seul représentant par diversification_group, le mieux classé."""
    seen: set[str] = set()
    kept: list[str] = []
    for chunk_id in chunk_ids:
        group = by_id[chunk_id].diversification_group
        if group in seen:
            continue
        seen.add(group)
        kept.append(chunk_id)
    return kept
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_dedup.py -v`
Attendu : 6 PASS

- [ ] **Step 5 : commit**

```bash
git add retrieval/dedup.py tests/unit/test_dedup.py
git commit -m "feat(retrieval): version filtering and diversification"
```

---

## Task 7 : Reranker Cohere

**Files:**
- Create: `retrieval/reranker.py`
- Test: `tests/unit/test_reranker.py`

**Interfaces:**
- Consumes: `gateway.settings.Settings` (Task 1).
- Produces: protocole `Reranker` (méthode `rerank(query: str, documents: list[str], top_n: int) -> list[RerankResult]`), `RerankResult` (dataclass gelée : `index: int`, `score: float`), `AzureCohereReranker(settings, http_client=None)`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_reranker.py
import httpx

from gateway.settings import Settings
from retrieval.reranker import AzureCohereReranker, RerankResult


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        azure_ai_endpoint="https://x.services.ai.azure.com/openai/v1",
        azure_ai_api_key="cle",
        azure_model_reranking="Cohere-rerank-v4.0-pro",
    )


def test_appel_http_et_lecture_de_la_reponse():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("api-key")
        captured["body"] = httpx.Request("POST", request.url, content=request.content).content
        # Format Cohere v1, vérifié contre le vrai endpoint (spec § 2.1).
        return httpx.Response(200, json={
            "results": [{"index": 1, "relevance_score": 0.8527},
                        {"index": 0, "relevance_score": 0.1719}],
            "meta": {"billed_units": {"search_units": 1}},
        })

    reranker = AzureCohereReranker(
        _settings(), http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    got = reranker.rerank("colis endommagé", ["notice led", "procedure colis"], top_n=2)

    assert got == [RerankResult(index=1, score=0.8527), RerankResult(index=0, score=0.1719)]
    # La route vit sous /models, pas sous /openai/v1.
    assert captured["url"].startswith("https://x.services.ai.azure.com/models/v1/rerank")
    assert captured["api_key"] == "cle"


def test_documents_vides_aucun_appel():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("aucun appel HTTP attendu pour une liste vide")

    reranker = AzureCohereReranker(
        _settings(), http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert reranker.rerank("q", [], top_n=5) == []
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_reranker.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'retrieval.reranker'`

- [ ] **Step 3 : écrire l'implémentation**

```python
# retrieval/reranker.py
"""Rerank Cohere via Azure AI Foundry.

La route est POST {endpoint sans /openai/v1}/models/v1/rerank, en-tête api-key, format
Cohere v1 (vérifié — spec § 2.1). Le SDK openai ne sait pas appeler cette opération.
"""

from dataclasses import dataclass
from typing import Protocol

import httpx

from gateway.settings import Settings

RERANK_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class RerankResult:
    index: int  # position dans la liste documents envoyée
    score: float  # score absolu dans [0, 1]


class Reranker(Protocol):
    def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankResult]: ...


class AzureCohereReranker:
    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        self._url = f"{settings.azure_models_base_url}/models/v1/rerank"
        self._model = settings.azure_model_reranking
        self._headers = {
            "Content-Type": "application/json",
            "api-key": settings.azure_ai_api_key,
        }
        self._client = http_client or httpx.Client(timeout=RERANK_TIMEOUT_S)

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankResult]:
        """Retourne les documents réordonnés, score décroissant (l'API trie déjà)."""
        if not documents:
            return []
        payload = {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
        }
        response = self._client.post(self._url, json=payload, headers=self._headers)
        response.raise_for_status()
        return [
            RerankResult(index=int(item["index"]), score=float(item["relevance_score"]))
            for item in response.json()["results"]
        ]
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_reranker.py -v`
Attendu : 2 PASS

- [ ] **Step 5 : vérifier contre le vrai endpoint** (hors suite de tests — réseau + facturation)

```bash
uv run python -c "
from gateway.settings import get_settings
from retrieval.reranker import AzureCohereReranker
r = AzureCohereReranker(get_settings())
docs = ['Procedure SAV : colis recu endommage.', 'Fiche disjoncteur triphase 63 A.']
for item in r.rerank('que faire si un colis arrive endommage ?', docs, top_n=2):
    print(f'{item.score:.4f}  {docs[item.index][:45]}')
"
```

Attendu : la procédure SAV en tête avec un score > 0,8, la fiche disjoncteur < 0,2.

- [ ] **Step 6 : commit**

```bash
git add retrieval/reranker.py tests/unit/test_reranker.py
git commit -m "feat(retrieval): Cohere reranker over Azure AI Foundry"
```

---

## Task 8 : Routing des références exactes

**Files:**
- Create: `retrieval/routing.py`
- Test: `tests/unit/test_routing.py`

**Interfaces:**
- Consumes: `IndexedChunk` (Task 2), `version_key` (Task 6).
- Produces: `detect_reference(question: str) -> str | None`, `lookup_by_reference(chunks, reference) -> list[IndexedChunk]`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_routing.py
from retrieval.corpus import IndexedChunk
from retrieval.routing import detect_reference, lookup_by_reference


def _chunk(chunk_id: str, ref: str | None, version: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=chunk_id, document_id=chunk_id.split("#")[0], content="c", title="T",
        type_doc="fiche_technique", collection="fiches", version=version,
        date="2024-01-01", source="pdf", family_id=(ref or "fam"),
        diversification_group=(ref or "grp"), ref_produit=ref,
    )


CHUNKS = [
    _chunk("REF-8842-v1.0#0", "REF-8842", "1.0"),
    _chunk("REF-8842-v2.1#0", "REF-8842", "2.1"),
    _chunk("REF-1024-v1.0#0", "REF-1024", "1.0"),
    _chunk("proc-01-v1.0#0", None, "1.0"),
]


def test_detection_reference_seule():
    assert detect_reference("REF-8842") == "REF-8842"


def test_detection_reference_dans_une_phrase():
    assert detect_reference("fiche technique REF-8842") == "REF-8842"
    assert detect_reference("ref-8842 svp") == "REF-8842"  # casse indifférente


def test_pas_de_reference():
    assert detect_reference("que faire si un colis arrive endommagé ?") is None


def test_lookup_retourne_la_derniere_version_en_tete():
    got = lookup_by_reference(CHUNKS, "REF-8842")
    assert [c.chunk_id for c in got] == ["REF-8842-v2.1#0", "REF-8842-v1.0#0"]


def test_lookup_reference_absente():
    assert lookup_by_reference(CHUNKS, "REF-0000") == []
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_routing.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'retrieval.routing'`

- [ ] **Step 3 : écrire l'implémentation**

```python
# retrieval/routing.py
"""Routing des références exactes, hors du retrieval.

Si la question porte une REF-nnnn, on ne cherche pas : on connaît la clé. Lookup
déterministe par métadonnée, garantie à 100 % là où un bon classement ne l'est pas
(conception § « remplacé par un routing côté client »).
"""

import re

from retrieval.corpus import IndexedChunk
from retrieval.dedup import version_key

RE_REFERENCE = re.compile(r"\bREF-(\d{4})\b", re.IGNORECASE)


def detect_reference(question: str) -> str | None:
    """Retourne la référence normalisée (REF-nnnn) si la question en contient une."""
    match = RE_REFERENCE.search(question)
    return f"REF-{match.group(1)}" if match else None


def lookup_by_reference(
    chunks: list[IndexedChunk], reference: str
) -> list[IndexedChunk]:
    """Chunks portant cette ref_produit, la version la plus récente en tête."""
    found = [c for c in chunks if c.ref_produit == reference]
    return sorted(found, key=lambda c: version_key(c.version), reverse=True)
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_routing.py -v`
Attendu : 5 PASS

- [ ] **Step 5 : commit**

```bash
git add retrieval/routing.py tests/unit/test_routing.py
git commit -m "feat(retrieval): exact reference routing"
```

---

## Task 9 : Moteur de recherche complet

**Files:**
- Create: `retrieval/engine.py`
- Test: `tests/unit/test_engine.py`, `tests/integration/test_retrieval.py`

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: `Hit` (dataclass gelée : `chunk: IndexedChunk`, `rerank_score: float | None`), `SearchOutcome` (dataclass gelée : `hits: list[Hit]`, `is_refusal: bool`, `reason: str | None`, `route: str`, `stages: dict[str, list[str]]`), `SearchEngine(collection, embedder, settings, reranker=None)` avec `search(question: str, top_k: int | None = None) -> SearchOutcome`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_engine.py
import chromadb

from gateway.settings import Settings
from retrieval.engine import SearchEngine
from retrieval.reranker import RerankResult


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class FakeReranker:
    """Score piloté par le test, indexé sur le contenu du document."""

    def __init__(self, scores_by_text: dict[str, float]) -> None:
        self.scores = scores_by_text

    def rerank(self, query, documents, top_n):
        scored = [
            RerankResult(index=i, score=self.scores.get(doc, 0.0))
            for i, doc in enumerate(documents)
        ]
        return sorted(scored, key=lambda r: r.score, reverse=True)[:top_n]


def _collection():
    client = chromadb.EphemeralClient()
    col = client.get_or_create_collection(name="engine_test", embedding_function=None)
    col.upsert(
        ids=["colis-v1.0#0", "colis-v2.0#0", "led#0"],
        embeddings=[[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]],
        documents=["procedure colis endommage", "procedure colis endommage v2", "notice led"],
        metadatas=[
            {"document_id": "colis-v1.0", "chunk_index": 0, "family_id": "colis",
             "diversification_group": "sav_casse", "title": "Colis endommagé",
             "type_doc": "procedure_sav", "collection": "sav", "version": "1.0",
             "date": "2025-01-01", "source": "html"},
            {"document_id": "colis-v2.0", "chunk_index": 0, "family_id": "colis",
             "diversification_group": "sav_casse", "title": "Colis endommagé",
             "type_doc": "procedure_sav", "collection": "sav", "version": "2.0",
             "date": "2026-04-05", "source": "html"},
            {"document_id": "led", "chunk_index": 0, "family_id": "led",
             "diversification_group": "notice_led", "title": "Projecteur LED",
             "type_doc": "notice", "collection": "notices", "version": "1.0",
             "date": "2024-03-23", "source": "pdf", "ref_produit": "REF-1459"},
        ],
    )
    return col


def _engine(reranker=None, **overrides):
    settings = Settings(_env_file=None, **overrides)
    return SearchEngine(_collection(), FakeEmbedder(), settings, reranker=reranker)


def test_question_couverte_retourne_des_resultats():
    reranker = FakeReranker({"procedure colis endommage v2": 0.85, "notice led": 0.10})
    out = _engine(reranker).search("que faire si un colis arrive endommage ?")
    assert out.is_refusal is False
    assert out.route == "hybrid"
    assert out.hits[0].chunk.title == "Colis endommagé"
    assert out.hits[0].rerank_score == 0.85


def test_hors_corpus_refuse_sous_le_seuil():
    reranker = FakeReranker({})  # tout à 0.0
    out = _engine(reranker).search("quelle est la politique de teletravail ?")
    assert out.is_refusal is True
    assert out.reason and "seuil" in out.reason
    assert out.hits == []


def test_versions_dedupliquees_derniere_gardee():
    reranker = FakeReranker({"procedure colis endommage v2": 0.85})
    out = _engine(reranker).search("colis endommage")
    ids = [h.chunk.chunk_id for h in out.hits]
    assert "colis-v1.0#0" not in ids  # v1.0 écartée par le filtrage de version
    assert "colis-v2.0#0" in ids


def test_reference_exacte_court_circuite_le_retrieval():
    out = _engine(FakeReranker({})).search("fiche REF-1459")
    assert out.route == "reference"
    assert out.is_refusal is False  # routing déterministe : jamais de refus
    assert out.hits[0].chunk.ref_produit == "REF-1459"
    assert out.hits[0].rerank_score is None


def test_sans_rerank_pas_de_refus():
    # Sans reranker il n'existe pas de signal de refus fiable (spec § 4.3) :
    # le moteur retourne des résultats sans décider.
    out = _engine(None, rerank_enabled=False).search("quelle est la politique de teletravail ?")
    assert out.is_refusal is False
    assert out.hits
    assert all(h.rerank_score is None for h in out.hits)
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_engine.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'retrieval.engine'`

- [ ] **Step 3 : écrire l'implémentation**

```python
# retrieval/engine.py
"""Orchestration du retrieval hybride.

Chaque étape produit une liste de chunk_id ordonnée, conservée dans `stages` pour
l'affichage pédagogique de l'agent (--show-stages).
"""

from dataclasses import dataclass, field

from chromadb.api.models.Collection import Collection

from gateway.settings import Settings
from gateway.embedder import Embedder
from retrieval.corpus import IndexedChunk, by_chunk_id, load_chunks
from retrieval.dedup import diversify, keep_latest_version
from retrieval.dense import dense_search
from retrieval.fusion import reciprocal_rank_fusion
from retrieval.lexical import LexicalIndex
from retrieval.reranker import Reranker
from retrieval.routing import detect_reference, lookup_by_reference


@dataclass(frozen=True)
class Hit:
    chunk: IndexedChunk
    rerank_score: float | None  # None si le rerank est désactivé ou hors chemin


@dataclass(frozen=True)
class SearchOutcome:
    hits: list[Hit]
    is_refusal: bool
    reason: str | None = None
    route: str = "hybrid"  # "reference" | "hybrid"
    stages: dict[str, list[str]] = field(default_factory=dict)


class SearchEngine:
    def __init__(
        self,
        collection: Collection,
        embedder: Embedder,
        settings: Settings,
        reranker: Reranker | None = None,
    ) -> None:
        self._collection = collection
        self._embedder = embedder
        self._settings = settings
        self._reranker = reranker
        # Index BM25 reconstruit au démarrage depuis Chroma (spec § 4.6).
        self._chunks = load_chunks(collection)
        self._by_id = by_chunk_id(self._chunks)
        self._lexical = LexicalIndex(self._chunks)

    def search(self, question: str, top_k: int | None = None) -> SearchOutcome:
        limit = top_k or self._settings.top_k
        reference = detect_reference(question)
        if reference is not None:
            return self._search_by_reference(reference, limit)
        return self._search_hybrid(question, limit)

    def _search_by_reference(self, reference: str, limit: int) -> SearchOutcome:
        found = lookup_by_reference(self._chunks, reference)
        return SearchOutcome(
            hits=[Hit(chunk=c, rerank_score=None) for c in found[:limit]],
            is_refusal=False,  # lookup déterministe : pas de décision de pertinence
            reason=None if found else f"aucun document pour {reference}",
            route="reference",
            stages={"reference": [c.chunk_id for c in found]},
        )

    def _search_hybrid(self, question: str, limit: int) -> SearchOutcome:
        cfg = self._settings
        dense = dense_search(
            self._collection, self._embedder, question, cfg.dense_candidates
        )
        lexical = self._lexical.search(question, cfg.lexical_candidates)
        fused = reciprocal_rank_fusion(
            [dense, lexical], k=cfg.rrf_k, limit=cfg.fusion_candidates
        )
        versioned = keep_latest_version(fused, self._by_id)
        diversified = diversify(versioned, self._by_id)
        stages = {
            "dense": dense,
            "lexical": lexical,
            "fused": fused,
            "versioned": versioned,
            "diversified": diversified,
        }

        if self._reranker is None or not cfg.rerank_enabled:
            # Sans rerank, aucun score absolu : pas de décision de refus (spec § 4.3).
            hits = [Hit(chunk=self._by_id[cid], rerank_score=None)
                    for cid in diversified[:limit]]
            return SearchOutcome(hits=hits, is_refusal=False, route="hybrid", stages=stages)

        candidates = diversified[: cfg.rerank_candidates]
        results = self._reranker.rerank(
            question,
            [self._by_id[cid].content for cid in candidates],
            top_n=cfg.rerank_candidates,
        )
        reranked = [
            Hit(chunk=self._by_id[candidates[r.index]], rerank_score=r.score)
            for r in results
        ]
        stages["reranked"] = [h.chunk.chunk_id for h in reranked]

        best = reranked[0].rerank_score if reranked else 0.0
        if best is None or best < cfg.refusal_threshold:
            return SearchOutcome(
                hits=[],
                is_refusal=True,
                reason=(f"pertinence insuffisante : meilleur score {best:.3f} "
                        f"sous le seuil de {cfg.refusal_threshold:.2f}"),
                route="hybrid",
                stages=stages,
            )
        return SearchOutcome(
            hits=reranked[:limit], is_refusal=False, route="hybrid", stages=stages
        )
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_engine.py -v`
Attendu : 5 PASS

- [ ] **Step 5 : écrire le test d'intégration sur le vrai corpus**

```python
# tests/integration/test_retrieval.py
"""Intégration : pipeline complet sur le corpus réel, sans réseau.

Chroma éphémère peuplé par l'ingestion, embedder et reranker factices.
"""

from pathlib import Path

import chromadb
import pytest

from gateway.settings import Settings
from ingest.pipeline import ingest_corpus
from gateway.chroma import open_collection
from retrieval.engine import SearchEngine
from retrieval.reranker import RerankResult
from retrieval.tokenize import tokenize

CORPUS = Path("data/corpus")


class FakeEmbedder:
    """Embedding jouet mais discriminant : sac de mots projeté sur 16 dimensions."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * 16
            for token in tokenize(text):
                vec[hash(token) % 16] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


class LexicalOverlapReranker:
    """Score = recouvrement de jetons question/document, dans [0, 1]."""

    def rerank(self, query, documents, top_n):
        q = set(tokenize(query))
        scored = []
        for i, doc in enumerate(documents):
            d = set(tokenize(doc))
            scored.append(RerankResult(index=i, score=len(q & d) / (len(q) or 1)))
        return sorted(scored, key=lambda r: r.score, reverse=True)[:top_n]


@pytest.fixture(scope="module")
def collection():
    client = chromadb.EphemeralClient()
    col = open_collection(client, "retrieval_integration_test")
    assert ingest_corpus(CORPUS, col, FakeEmbedder()) == 400
    return col


@pytest.fixture(scope="module")
def engine(collection):
    settings = Settings(_env_file=None, refusal_threshold=0.0)
    return SearchEngine(
        collection, FakeEmbedder(), settings, reranker=LexicalOverlapReranker()
    )


def test_reference_exacte_trouve_la_fiche(engine):
    out = engine.search("REF-8842")
    assert out.route == "reference"
    assert out.hits
    assert out.hits[0].chunk.ref_produit == "REF-8842"
    # La dernière version en tête : le corpus a REF-8842 en v1.0 et v2.1.
    assert out.hits[0].chunk.version == "2.1"


def test_question_couverte_passe_par_l_hybride(engine):
    out = engine.search("que faire si un colis arrive endommagé ?")
    assert out.route == "hybrid"
    assert out.is_refusal is False
    assert out.hits
    assert {"dense", "lexical", "fused", "reranked"} <= set(out.stages)


def test_aucun_doublon_de_famille_ni_de_groupe(engine):
    out = engine.search("procédure de retour d'un produit défectueux")
    familles = [h.chunk.family_id for h in out.hits]
    groupes = [h.chunk.diversification_group for h in out.hits]
    assert len(familles) == len(set(familles))
    assert len(groupes) == len(set(groupes))


def test_seuil_haut_declenche_le_refus(collection):
    haut = Settings(_env_file=None, refusal_threshold=0.99)
    strict = SearchEngine(
        collection, FakeEmbedder(), haut, reranker=LexicalOverlapReranker()
    )
    out = strict.search("quelle est la politique de télétravail chez Sorabel ?")
    assert out.is_refusal is True
    assert out.hits == []
```

- [ ] **Step 6 : lancer les tests d'intégration**

Run : `uv run pytest tests/integration/test_retrieval.py -v`
Attendu : 4 PASS

- [ ] **Step 7 : lint, typage, commit**

Run : `uv run ruff check . && uv run mypy gateway ingest retrieval`

```bash
git add retrieval/engine.py tests/unit/test_engine.py tests/integration/test_retrieval.py
git commit -m "feat(retrieval): hybrid search engine with refusal decision"
```

---

## Task 10 : Rédaction de la réponse et agent CLI

**Files:**
- Create: `retrieval/answer.py`, `scripts/demo_agent.py`
- Modify: `Makefile`
- Test: `tests/unit/test_answer.py`

**Interfaces:**
- Consumes: `Hit`, `SearchOutcome` (Task 9), `Settings` (Task 1).
- Produces: `format_citation(chunk) -> str`, `build_context(hits) -> str`, `ANSWER_SYSTEM_PROMPT`, `compose_answer(client, model, question, hits) -> str`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_answer.py
from retrieval.answer import ANSWER_SYSTEM_PROMPT, build_context, compose_answer, format_citation
from retrieval.corpus import IndexedChunk
from retrieval.engine import Hit


def _hit(title: str, ref: str | None) -> Hit:
    return Hit(
        chunk=IndexedChunk(
            chunk_id="c#0", document_id="c", content="Le délai est de 5 jours ouvrés.",
            title=title, type_doc="procedure_sav", collection="sav", version="2.0",
            date="2026-04-05", source="html", family_id="f",
            diversification_group="g", ref_produit=ref,
        ),
        rerank_score=0.85,
    )


class FakeLLM:
    """Double du client OpenAI : capture les messages, renvoie une réponse fixe."""

    def __init__(self) -> None:
        self.captured: dict = {}

        class Completions:
            def create(inner, **kwargs):  # noqa: N805
                self.captured = kwargs

                class Msg:
                    content = "Le délai est de 5 jours ouvrés (Colis endommagé, 2026-04-05)."

                class Choice:
                    message = Msg()

                class Response:
                    choices = [Choice()]

                return Response()

        class Chat:
            completions = Completions()

        self.chat = Chat()


def test_citation_contient_titre_date_et_reference():
    assert format_citation(_hit("Colis endommagé", "REF-8842").chunk) == (
        "Colis endommagé — REF-8842 — 2026-04-05"
    )


def test_citation_sans_reference():
    # sav/ et notes/ n'ont pas de ref_produit : la citation reste valide (E1).
    assert format_citation(_hit("Colis endommagé", None).chunk) == (
        "Colis endommagé — 2026-04-05"
    )


def test_contexte_numerote_les_passages():
    ctx = build_context([_hit("A", None), _hit("B", "REF-1")])
    assert "[1] A — 2026-04-05" in ctx
    assert "[2] B — REF-1 — 2026-04-05" in ctx


def test_prompt_interdit_de_repondre_hors_passages():
    assert "uniquement" in ANSWER_SYSTEM_PROMPT.lower()


def test_compose_answer_utilise_max_completion_tokens():
    llm = FakeLLM()
    out = compose_answer(llm, "gpt-5.4-mini", "quel délai ?", [_hit("Colis endommagé", None)])
    assert "5 jours" in out
    # gpt-5.4-mini refuse max_tokens (spec § 2.4).
    assert "max_completion_tokens" in llm.captured
    assert "max_tokens" not in llm.captured
    assert llm.captured["model"] == "gpt-5.4-mini"
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_answer.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'retrieval.answer'`

- [ ] **Step 3 : écrire l'implémentation**

```python
# retrieval/answer.py
"""Rédaction de la réponse sourcée.

La génération est côté client, hors du retrieval : le moteur retourne des passages,
l'agent compose (conception « LLM côté client, hors MCP »).
"""

from typing import Any

from retrieval.corpus import IndexedChunk
from retrieval.engine import Hit

MAX_ANSWER_TOKENS = 700

ANSWER_SYSTEM_PROMPT = """\
Tu réponds à des questions internes de Sorabel, distributeur de matériel électrique.

Règles impératives :
- Réponds UNIQUEMENT à partir des passages fournis. N'ajoute aucune connaissance externe.
- Cite tes sources en fin de phrase, sous la forme (titre — référence — date).
- Si les passages ne permettent pas de répondre, dis-le explicitement au lieu d'inventer.
- Réponds en français, de façon concise et opérationnelle.\
"""


def format_citation(chunk: IndexedChunk) -> str:
    parts = [chunk.title]
    if chunk.ref_produit:
        parts.append(chunk.ref_produit)
    parts.append(chunk.date)
    return " — ".join(parts)


def build_context(hits: list[Hit]) -> str:
    blocks = []
    for position, hit in enumerate(hits, start=1):
        blocks.append(f"[{position}] {format_citation(hit.chunk)}\n{hit.chunk.content}")
    return "\n\n".join(blocks)


def compose_answer(client: Any, model: str, question: str, hits: list[Hit]) -> str:
    """Appelle le LLM avec les passages retenus. `client` a la forme du SDK openai."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Question : {question}\n\nPassages :\n{build_context(hits)}"},
        ],
        max_completion_tokens=MAX_ANSWER_TOKENS,  # gpt-5.4-mini refuse max_tokens
    )
    return str(response.choices[0].message.content or "").strip()
```

```python
# scripts/demo_agent.py
"""Agent de démonstration du RAG hybride avec reranking.

    uv run python scripts/demo_agent.py "que faire si un colis arrive endommagé ?"
    uv run python scripts/demo_agent.py --no-rerank "…"     # montre l'apport du rerank
    uv run python scripts/demo_agent.py --show-stages "…"   # détaille le pipeline
    uv run python scripts/demo_agent.py                     # boucle interactive
"""

import argparse

from openai import OpenAI

from gateway.settings import get_settings
from gateway.chroma import chroma_client, open_collection
from gateway.embedder import AzureEmbedder
from retrieval.answer import compose_answer, format_citation
from retrieval.engine import SearchEngine, SearchOutcome
from retrieval.reranker import AzureCohereReranker

STAGE_LABELS = {
    "dense": "1. Dense (Chroma)",
    "lexical": "2. BM25 (lexical)",
    "fused": "3. Fusion RRF",
    "versioned": "4. Dernière version par famille",
    "diversified": "5. Diversification par thème",
    "reranked": "6. Rerank Cohere",
    "reference": "0. Routing par référence exacte",
}


def show_stages(outcome: SearchOutcome) -> None:
    print("\n--- Étapes du pipeline ---")
    for key, label in STAGE_LABELS.items():
        if key in outcome.stages:
            ids = outcome.stages[key]
            print(f"{label:38} {len(ids):3} candidats : {', '.join(ids[:3])}…")


def render(outcome: SearchOutcome, answer: str | None) -> None:
    print(f"\nRoute : {outcome.route}")
    if outcome.is_refusal:
        print(f"\n❌ REFUS — {outcome.reason}")
        return
    if not outcome.hits:
        print(f"\n(aucun résultat — {outcome.reason})")
        return
    print("\n--- Passages retenus ---")
    for position, hit in enumerate(outcome.hits, start=1):
        score = f"{hit.rerank_score:.4f}" if hit.rerank_score is not None else "  —   "
        print(f"[{position}] score={score}  {format_citation(hit.chunk)}")
    if answer:
        print(f"\n--- Réponse ---\n{answer}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Démo du RAG hybride Sorabel")
    parser.add_argument("question", nargs="?", help="question ; absente = mode interactif")
    parser.add_argument("--no-rerank", action="store_true", help="désactive le reranking")
    parser.add_argument("--show-stages", action="store_true", help="détaille le pipeline")
    parser.add_argument("--no-answer", action="store_true", help="passages seuls, sans LLM")
    args = parser.parse_args()

    settings = get_settings()
    if args.no_rerank:
        settings = settings.model_copy(update={"rerank_enabled": False})
    collection = open_collection(chroma_client(settings), settings.chroma_collection)
    reranker = None if args.no_rerank else AzureCohereReranker(settings)
    engine = SearchEngine(collection, AzureEmbedder(settings), settings, reranker=reranker)
    llm = OpenAI(base_url=settings.azure_ai_endpoint, api_key=settings.azure_ai_api_key)

    def handle(question: str) -> None:
        outcome = engine.search(question)
        if args.show_stages:
            show_stages(outcome)
        answer = None
        if outcome.hits and not args.no_answer:
            answer = compose_answer(
                llm, settings.azure_model_text_generation, question, outcome.hits
            )
        render(outcome, answer)

    if args.question:
        handle(args.question)
        return
    print("Mode interactif — Ctrl-D pour quitter.")
    while True:
        try:
            question = input("\nQuestion > ").strip()
        except EOFError:
            print()
            return
        if question:
            handle(question)


if __name__ == "__main__":
    main()
```

Dans le `Makefile`, ajouter `demo` à `.PHONY` et la cible :

```makefile
demo:
	uv run python scripts/demo_agent.py $${Q:+"$$Q"}
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_answer.py -v`
Attendu : 5 PASS

- [ ] **Step 5 : vérifier l'agent de bout en bout** (réseau + facturation)

```bash
uv run python scripts/demo_agent.py --show-stages "que faire si un colis arrive endommagé ?"
uv run python scripts/demo_agent.py "quelle est la politique de télétravail chez Sorabel ?"
uv run python scripts/demo_agent.py --no-rerank "quelle norme s'applique aux disjoncteurs modulaires ?"
uv run python scripts/demo_agent.py "REF-8842"
```

Attendu : question couverte → passages + réponse sourcée ; hors corpus → refus explicite ;
`--no-rerank` → résultats sans score ni refus ; `REF-8842` → route `reference`, version 2.1.

- [ ] **Step 6 : lint, typage, commit**

Run : `uv run ruff check . && uv run mypy gateway ingest retrieval`

```bash
git add retrieval/answer.py scripts/demo_agent.py Makefile tests/unit/test_answer.py
git commit -m "feat(retrieval): sourced answer composition and demo agent CLI"
```

---

## Task 11 : Calibration du seuil et rapport E6

Cette tâche peut atterrir **après** la démonstration si le temps manque (voir Priorités).

**Files:**
- Create: `scripts/eval_rag.py`
- Create: `eval/rapport_gain.md` (généré)
- Modify: `Makefile`, `docs/CHANGELOG.md`

**Interfaces:**
- Consumes: `SearchEngine` (Task 9).
- Produces: script exécutable, rapport Markdown.

- [ ] **Step 1 : écrire le script d'évaluation**

```python
# scripts/eval_rag.py
"""Mesure du gain hybride/rerank sur eval/questions_rag.jsonl (E6) et calibration
du seuil de refus (E1).

    uv run python scripts/eval_rag.py            # rapport dans eval/rapport_gain.md
"""

import json
from dataclasses import dataclass
from pathlib import Path

from gateway.settings import get_settings
from gateway.chroma import chroma_client, open_collection
from gateway.embedder import AzureEmbedder
from retrieval.engine import SearchEngine
from retrieval.reranker import AzureCohereReranker

EVAL_FILE = Path("eval/questions_rag.jsonl")
REPORT_FILE = Path("eval/rapport_gain.md")
SEUILS = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70]


@dataclass
class Config:
    label: str
    dense_only: bool
    rerank: bool


CONFIGS = [
    Config("A — dense seul", dense_only=True, rerank=False),
    Config("B — hybride (Dense+BM25+RRF)", dense_only=False, rerank=False),
    Config("C — hybride + rerank", dense_only=False, rerank=True),
]


def load_questions() -> list[dict]:
    return [json.loads(line) for line in EVAL_FILE.read_text("utf-8").splitlines() if line.strip()]


def build_engine(config: Config, settings, collection, embedder):
    """Moteur pour une configuration donnée.

    refusal_threshold=0.0 : on mesure ici la qualité du classement, le seuil de refus
    est calibré séparément à partir de la distribution des scores.
    """
    updates: dict = {"rerank_enabled": config.rerank, "refusal_threshold": 0.0}
    if config.dense_only:
        updates["lexical_candidates"] = 0  # neutralise la piste BM25
    tuned = settings.model_copy(update=updates)
    reranker = AzureCohereReranker(tuned) if config.rerank else None
    return SearchEngine(collection, embedder, tuned, reranker=reranker)


def hit_ok(question: dict, hits) -> bool:
    if "attendu_reference" in question:
        return any(h.chunk.ref_produit == question["attendu_reference"] for h in hits)
    if "attendu_type" in question:
        return any(h.chunk.type_doc == question["attendu_type"] for h in hits)
    return False


def main() -> None:
    settings = get_settings()
    collection = open_collection(chroma_client(settings), settings.chroma_collection)
    embedder = AzureEmbedder(settings)
    questions = load_questions()
    couvertes = [q for q in questions if q["type"] == "couverte"]
    references = [q for q in questions if q["type"] == "reference_exacte"]
    hors = [q for q in questions if q["type"] == "hors_corpus"]

    lines = ["# Rapport de gain — recherche avancée vs recherche simple (E6)", ""]
    lines += [f"Jeu d'évaluation : `{EVAL_FILE}` — {len(questions)} questions "
              f"({len(couvertes)} couvertes, {len(references)} par référence exacte, "
              f"{len(hors)} hors corpus).", ""]
    lines += ["| Configuration | Couvertes top-1 | Couvertes top-5 | Références exactes |",
              "|---|---|---|---|"]

    scores_hors: list[float] = []
    scores_couvertes: list[float] = []
    for config in CONFIGS:
        engine = build_engine(config, settings, collection, embedder)
        top1 = sum(hit_ok(q, engine.search(q["question"], top_k=1).hits) for q in couvertes)
        top5 = sum(hit_ok(q, engine.search(q["question"], top_k=5).hits) for q in couvertes)
        refs = sum(hit_ok(q, engine.search(q["question"]).hits) for q in references)
        lines.append(f"| {config.label} | {top1}/{len(couvertes)} | {top5}/{len(couvertes)} "
                     f"| {refs}/{len(references)} |")
        if config.rerank:
            for q in hors:
                out = engine.search(q["question"])
                scores_hors.append(max((h.rerank_score or 0.0) for h in out.hits) if out.hits else 0.0)
            for q in couvertes:
                out = engine.search(q["question"])
                scores_couvertes.append(max((h.rerank_score or 0.0) for h in out.hits) if out.hits else 0.0)

    lines += ["", "## Calibration du seuil de refus (E1)", "",
              "Le seuil porte sur le score du reranker, jamais sur un score de fusion "
              "(le score RRF classe un hors-corpus plus haut qu'une question couverte).", "",
              "| Seuil | Hors corpus refusés | Couvertes refusées à tort |", "|---|---|---|"]
    for seuil in SEUILS:
        refuses = sum(1 for s in scores_hors if s < seuil)
        faux = sum(1 for s in scores_couvertes if s < seuil)
        lines.append(f"| {seuil:.2f} | {refuses}/{len(scores_hors)} | {faux}/{len(scores_couvertes)} |")

    parfaits = [s for s in SEUILS
                if all(x < s for x in scores_hors) and all(x >= s for x in scores_couvertes)]
    if parfaits:
        lines += ["", f"**Seuil retenu : {parfaits[len(parfaits) // 2]:.2f}** — refuse tous les "
                      "hors-corpus sans refuser aucune question couverte."]
    else:
        lines += ["", "**Aucun seuil ne sépare parfaitement les deux populations sur ce jeu.** "
                      "Le compromis retenu est documenté ici plutôt que masqué : voir le tableau "
                      "ci-dessus pour choisir entre rappel et précision du refus."]

    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"rapport écrit dans {REPORT_FILE}")


if __name__ == "__main__":
    main()
```

Dans le `Makefile`, ajouter `eval` à `.PHONY` et la cible :

```makefile
eval:
	uv run python scripts/eval_rag.py
```

- [ ] **Step 2 : lancer l'évaluation**

Run : `make eval`
Attendu : `eval/rapport_gain.md` écrit, avec les trois configurations comparées.

- [ ] **Step 3 : appliquer le seuil calibré**

Si le rapport désigne un seuil parfait différent de 0,40, mettre à jour `refusal_threshold`
dans `gateway/settings.py` (valeur par défaut) et le mentionner dans le rapport.

- [ ] **Step 4 : vérifier les critères de la spec § 7**

```bash
uv run python scripts/demo_agent.py "quelle est la politique de télétravail chez Sorabel ?"  # refus
uv run python scripts/demo_agent.py "quelle norme s'applique aux disjoncteurs modulaires ?"  # réponse
uv run pytest tests/unit tests/integration -q
```

- [ ] **Step 5 : consigner dans le changelog et committer**

Ajouter en tête de `docs/CHANGELOG.md` une entrée datée : modules construits, chiffres du
rapport de gain (top-1/top-5 par configuration), seuil retenu, et ce qui reste ouvert.

```bash
git add scripts/eval_rag.py eval/rapport_gain.md Makefile gateway/settings.py docs/CHANGELOG.md
git commit -m "feat(eval): hybrid gain measurement and refusal threshold calibration"
```

---

## Ce que ce plan ne fait pas

- Aucun serveur MCP ni transport stdio : l'agent appelle le moteur en direct, en Python.
- Aucun tool `list_sources` / `get_document` / `answer_question` exposé : le routing par
  référence fait le lookup en interne (spec § 8). L'assemblage en tools relève du chantier MCP.
- Aucune modification de `tests/acceptance/` ni de `tests/conftest.py` — ils encodent le contrat
  de `docs/cadrage_dsi.md`, retiré par le formateur, à trancher avec lui avant le chantier MCP.
- Aucune interface graphique (livrable exigé au brief, mais hors de cette étape).
