# Plan d'implémentation — Serveur MCP, matrice d'accès et journal unique

> **Pour un worker agentique :** SOUS-SKILL REQUIS : utiliser
> `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans`
> pour dérouler ce plan tâche par tâche. Les étapes sont en cases à cocher (`- [ ]`).

**Objectif :** exposer les 8 tools déjà conçus (4 RAG, 4 SQL) à travers un serveur MCP
unique (`mcp_server.server`, transport stdio), avec profil résolu côté serveur, matrice
d'accès, journal unique et contrat d'erreur commun.

**Architecture :** un module par responsabilité dans `mcp_server/` (même patron que
`sql/` et `retrieval/`), assemblant `SearchEngine` et `SqlEngine` **sans les modifier**
(sauf un ajout mineur, Task 1). La logique de dispatch (`dispatch()`) est une fonction
pure séparée du branchement au SDK MCP (`main()`), pour être testée sans passer par le
protocole stdio réel.

**Stack :** Python 3.11, SDK `mcp>=1.2,<2` (déjà une dépendance), `pyyaml` (nouvelle
dépendance, matrice déclarative), `streamlit` (GUI), `pytest`, `ruff`, `mypy`.

**Spec de référence :** `docs/spec_mcp.md`. Conception amont :
`conception/3_MCP/questions_reponses_mcp.md`, `conception/commun/catalogue_tools_mcp.md`,
`conception/commun/journal_mcp.md`.

## Contraintes globales

- Ligne max **100** caractères (`ruff`), cible `py311`. `mypy` doit passer sur
  `gateway ingest retrieval sql mcp_server` (`make lint`).
- Code en **anglais**, commentaires et docstrings en **français**, style loquace
  (rôle, pourquoi ce choix, qui consomme quoi) — voir `sql/engine.py`, `sql/access.py`.
- **Ne pas modifier** `retrieval/engine.py` au-delà de la Task 1 (un seul champ ajouté),
  ni `sql/` (chantiers 1 et 2 terminés, déjà testés).
- Le profil n'est **jamais** un paramètre de tool — résolu une fois au démarrage du
  process serveur (`SORABEL_PROFILE`), injecté aux moteurs à la construction.
- Journal unique `logs/mcp_audit.jsonl` (chemin réel), surchargé par `GATEWAY_JOURNAL`
  (contrat `tests/conftest.py`). Clés françaises (`profil`, `statut`, `code`, `detail`),
  alignées sur ce que `sql/engine.py` écrit déjà réellement — pas sur la forme nichée
  (`motif` + `detail` objet) de `journal_mcp.md`, jamais implémentée ainsi (§ spec_mcp § 4.5).
- `ask_database` ne renvoie jamais le SQL généré/exécuté au client (décision assumée,
  spec_mcp.md § 4.1) — seul le journal le porte (déjà le cas, `sql/engine.py`).
- Aucun tool n'est refusé dans son intégralité pour un profil (spec_mcp.md § 4.3) — la
  seule restriction réelle porte sur 3 colonnes SQL (`sql/access.py:SENSITIVE_COLUMNS`).
- Commit après **chaque** tâche, après `uv run ruff check .`, `uv run mypy gateway ingest
  retrieval sql mcp_server` et `uv run pytest tests/unit tests/integration -q` au vert.
  La suite `tests/acceptance` n'est vérifiée qu'à la Task 7 (nécessite `make seed`,
  `make up`, des credentials Azure valides, et le serveur complet).

### API du SDK `mcp` installé (vérifiée dans `.venv`, `mcp==1.x`)

```python
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

server = Server("name", instructions="...")  # "instructions" = Server Instructions

@server.list_tools()
async def _list_tools() -> list[types.Tool]: ...

@server.call_tool()
async def _call_tool(name: str, arguments: dict) -> types.CallToolResult: ...
# Retourner un types.CallToolResult directement est supporté — il est renvoyé tel quel.

types.Tool(name=..., description=..., inputSchema={...}, meta={...})       # meta -> "_meta" sur le fil
types.CallToolResult(content=[...], isError=bool, meta={...} | None)       # idem
types.TextContent(type="text", text="...")

async with stdio_server() as (read, write):
    await server.run(read, write, server.create_initialization_options())
```

`Tool.meta` et `CallToolResult.meta` ont l'alias pydantic `_meta` — c'est ce nom qui
apparaît sur le fil et dans `tests/conftest.py`/`scripts/mcp_client.py`.

---

## Task 1 : `SearchDocResult` porte le type de document

**Files :**
- Modify : `retrieval/engine.py:75-90` (classe `SearchDocResult`, méthode `search_docs`)
- Test : `tests/unit/test_engine.py`

**Interfaces :**
- Produit : `SearchDocResult.type_doc: str` — nouveau champ, consommé par
  `mcp_server/envelope.py::search_docs_envelope` (Task 4).

Le payload MCP de `search_docs` (contrat `tests/conftest.py`, non modifié — voir
spec_mcp.md § 2, point 4/§ 5) attend `metadata.doc_type` par résultat. `SearchDocResult`
ne porte aujourd'hui que `title`, `ref_produit`, `version`, `date`, `source`, `content` —
pas `type_doc`, pourtant déjà présent sur `IndexedChunk`. Seul site de construction
(`retrieval/engine.py:232`), donc ajout sans risque de régression ailleurs.

- [ ] **Step 1 : Étendre le test existant**

Dans `tests/unit/test_engine.py`, dans `test_search_docs_brut_sans_dedup_ni_refus`,
ajouter après la ligne `assert all(r.rrf_score is None for r in out.results)` :

```python
    assert all(r.type_doc for r in out.results)
```

- [ ] **Step 2 : Lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_engine.py::test_search_docs_brut_sans_dedup_ni_refus -v`
Attendu : `AttributeError: 'SearchDocResult' object has no attribute 'type_doc'`

- [ ] **Step 3 : Ajouter le champ**

Dans `retrieval/engine.py`, classe `SearchDocResult` (juste après `title: str`) :

```python
    type_doc: str
```

Et dans `search_docs()`, dans la construction de `SearchDocResult` (après `title=...`) :

```python
                title=by_id[chunk_id].title,
                type_doc=by_id[chunk_id].type_doc,
```

- [ ] **Step 4 : Lancer le test, vérifier qu'il passe**

Run : `uv run pytest tests/unit/test_engine.py -v`
Attendu : tous les tests de ce fichier PASS.

- [ ] **Step 5 : Commit**

```bash
git add retrieval/engine.py tests/unit/test_engine.py
git commit -m "feat(retrieval): expose type_doc on SearchDocResult for the MCP payload"
```

---

## Task 2 : Matrice d'accès déclarative (`mcp_server/access.py`)

**Files :**
- Create : `mcp_server/matrice_acces.yaml`
- Create : `mcp_server/access.py`
- Modify : `pyproject.toml` (ajout dépendance `pyyaml`)
- Test : `tests/unit/test_mcp_access.py`

**Interfaces :**
- Consomme : `sql.access.PROFILES` (`frozenset({"support", "commercial"})`).
- Produit : `load_matrix(path=DEFAULT_MATRIX_PATH) -> dict[str, ProfileRules]`,
  `YamlAccessRules(matrix)` avec `.hidden_columns(profile) -> frozenset[tuple[str,str]]`
  (implémente `sql.access.AccessRules`), `.allowed_tools(profile) -> frozenset[str]`,
  `.allowed_collections(profile) -> frozenset[str]`. Consommé par `mcp_server/server.py`
  (Task 6, injecté dans `SqlEngine`) et `mcp_server/catalogue.py` (Task 5, `_meta`).

- [ ] **Step 1 : Ajouter la dépendance**

Dans `pyproject.toml`, section `dependencies`, ajouter après `"mcp>=1.2,<2",` :

```toml
    "pyyaml>=6.0,<7",
```

Run : `uv sync`

- [ ] **Step 2 : Écrire le fichier de matrice**

Créer `mcp_server/matrice_acces.yaml` :

```yaml
# Matrice d'accès Sorabel — source déclarative unique (conception/3_MCP § 1.3).
#
# Aujourd'hui, la seule restriction réelle du système porte sur 3 colonnes SQL
# (support). Aucun tool n'est interdit, aucune collection RAG n'est filtrée
# (docs/spec_retrieval.md § "hors périmètre" : décision confirmée par le formateur).
# Les dimensions tools/collections restent déclarées, complètes, pour rester lisibles
# et extensibles sans re-concevoir la matrice — jamais pour combler une case vide.
profiles:
  support:
    tools:
      - answer_question
      - search_docs
      - get_document
      - list_sources
      - ask_database
      - get_schema
      - check_stock
      - order_status
    rag_collections:
      - fiches_techniques
      - notices
      - procedures_sav
      - notes
    sql_hidden_columns:
      - [produits, prix_achat_ht]
      - [produits, marge_pct]
      - [ventes, marge_ht]
  commercial:
    tools:
      - answer_question
      - search_docs
      - get_document
      - list_sources
      - ask_database
      - get_schema
      - check_stock
      - order_status
    rag_collections:
      - fiches_techniques
      - notices
      - procedures_sav
      - notes
    sql_hidden_columns: []
```

- [ ] **Step 3 : Écrire le test (échoue, le module n'existe pas)**

Créer `tests/unit/test_mcp_access.py` :

```python
import pytest

from sql.access import SENSITIVE_COLUMNS


def test_la_matrice_reelle_se_charge_sans_erreur():
    from mcp_server.access import DEFAULT_MATRIX_PATH, load_matrix

    matrix = load_matrix(DEFAULT_MATRIX_PATH)
    assert set(matrix) == {"support", "commercial"}


def test_colonnes_cachees_support_coherentes_avec_sql_access():
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix

    rules = YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))
    assert rules.hidden_columns("support") == SENSITIVE_COLUMNS


def test_commercial_sans_colonne_cachee():
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix

    rules = YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))
    assert rules.hidden_columns("commercial") == frozenset()


def test_get_schema_accessible_aux_deux_profils():
    # Décision de cette session : aucun tool n'est interdit dans son intégralité
    # (spec_mcp.md § 2, point 1 / § 4.3) — contrairement à la matrice de
    # docs/cadrage_dsi.md, écartée.
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix

    rules = YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))
    assert "get_schema" in rules.allowed_tools("support")
    assert "get_schema" in rules.allowed_tools("commercial")


def test_profil_inconnu_leve_value_error():
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix

    rules = YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))
    with pytest.raises(ValueError, match="profil inconnu"):
        rules.hidden_columns("admin")


def test_load_matrix_rejette_un_profil_manquant(tmp_path):
    from mcp_server.access import load_matrix

    incomplet = tmp_path / "incomplet.yaml"
    incomplet.write_text(
        "profiles:\n  support:\n    tools: []\n    rag_collections: []\n"
        "    sql_hidden_columns: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="profils"):
        load_matrix(incomplet)


def test_load_matrix_rejette_un_profil_inconnu(tmp_path):
    from mcp_server.access import load_matrix

    en_trop = tmp_path / "en_trop.yaml"
    en_trop.write_text(
        "profiles:\n"
        "  support: {tools: [], rag_collections: [], sql_hidden_columns: []}\n"
        "  commercial: {tools: [], rag_collections: [], sql_hidden_columns: []}\n"
        "  admin: {tools: [], rag_collections: [], sql_hidden_columns: []}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="profils"):
        load_matrix(en_trop)
```

Run : `uv run pytest tests/unit/test_mcp_access.py -v`
Attendu : `ModuleNotFoundError: No module named 'mcp_server.access'`

- [ ] **Step 4 : Créer `mcp_server/__init__.py` s'il n'existe pas déjà**

Vérifier : `test -f mcp_server/__init__.py && echo présent || touch mcp_server/__init__.py`

- [ ] **Step 5 : Implémenter `mcp_server/access.py`**

```python
"""Matrice d'accès Sorabel — source déclarative unique, chargée une fois au démarrage.

Le fichier YAML (``matrice_acces.yaml``) est la présentation humaine et la source de
vérité machine à la fois (conception § 1.3 : « aucune permission n'est écrite deux
fois »). ``sql/access.py`` reste le seul ``Protocol`` que ``SqlEngine`` consomme
(``hidden_columns``) — ``YamlAccessRules`` l'implémente, en plus des deux dimensions
supplémentaires (``tools``, ``rag_collections``) qui n'ont aujourd'hui aucune
restriction réelle (spec_mcp.md § 2, point 4) mais restent déclarées pour rester
extensibles sans re-concevoir la matrice si un vrai besoin apparaît.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from sql.access import PROFILES

DEFAULT_MATRIX_PATH = Path(__file__).parent / "matrice_acces.yaml"


@dataclass(frozen=True)
class ProfileRules:
    tools: frozenset[str]
    rag_collections: frozenset[str]
    hidden_columns: frozenset[tuple[str, str]]


def load_matrix(path: Path = DEFAULT_MATRIX_PATH) -> dict[str, ProfileRules]:
    """Charge et valide la matrice : un profil manquant ou en trop est une erreur au
    démarrage, jamais un accès silencieusement ouvert ou fermé."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    declared = set(raw["profiles"])
    if declared != set(PROFILES):
        raise ValueError(
            f"profils de la matrice {sorted(declared)} != attendus {sorted(PROFILES)}"
        )
    matrix: dict[str, ProfileRules] = {}
    for profile, data in raw["profiles"].items():
        matrix[profile] = ProfileRules(
            tools=frozenset(data["tools"]),
            rag_collections=frozenset(data["rag_collections"]),
            hidden_columns=frozenset(tuple(pair) for pair in data["sql_hidden_columns"]),
        )
    return matrix


class YamlAccessRules:
    """Implémente ``sql.access.AccessRules`` ; expose aussi les dimensions tool et
    collection consommées par ``mcp_server/catalogue.py`` (``_meta["sorabel/roles"]``)."""

    def __init__(self, matrix: dict[str, ProfileRules]) -> None:
        self._matrix = matrix

    def hidden_columns(self, profile: str) -> frozenset[tuple[str, str]]:
        return self._rules(profile).hidden_columns

    def allowed_tools(self, profile: str) -> frozenset[str]:
        return self._rules(profile).tools

    def allowed_collections(self, profile: str) -> frozenset[str]:
        return self._rules(profile).rag_collections

    def _rules(self, profile: str) -> ProfileRules:
        if profile not in self._matrix:
            raise ValueError(f"profil inconnu : {profile!r}")
        return self._matrix[profile]
```

- [ ] **Step 6 : Lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_mcp_access.py -v`
Attendu : 7 PASS.

- [ ] **Step 7 : Lint et types**

Run : `uv run ruff check mcp_server tests/unit/test_mcp_access.py && uv run mypy mcp_server`

- [ ] **Step 8 : Commit**

```bash
git add pyproject.toml uv.lock mcp_server/access.py mcp_server/matrice_acces.yaml \
        mcp_server/__init__.py tests/unit/test_mcp_access.py
git commit -m "feat(mcp_server): load the access matrix from a declarative YAML file"
```

---

## Task 3 : Résolution d'identité (`mcp_server/identity.py`)

**Files :**
- Create : `mcp_server/identity.py`
- Test : `tests/unit/test_identity.py`

**Interfaces :**
- Consomme : `sql.access.PROFILES`.
- Produit : `IdentityResolver` (`Protocol`, méthode `resolve() -> str`),
  `EnvVarIdentityResolver`, `DEFAULT_PROFILE = "support"`. Consommé par
  `mcp_server/server.py::main()` (Task 6).

- [ ] **Step 1 : Écrire le test (échoue, le module n'existe pas)**

Créer `tests/unit/test_identity.py` :

```python
import pytest


def test_profil_par_defaut_si_variable_absente(monkeypatch):
    from mcp_server.identity import EnvVarIdentityResolver

    monkeypatch.delenv("SORABEL_PROFILE", raising=False)
    assert EnvVarIdentityResolver().resolve() == "support"


def test_lit_le_profil_depuis_la_variable(monkeypatch):
    from mcp_server.identity import EnvVarIdentityResolver

    monkeypatch.setenv("SORABEL_PROFILE", "commercial")
    assert EnvVarIdentityResolver().resolve() == "commercial"


def test_profil_invalide_leve_value_error(monkeypatch):
    from mcp_server.identity import EnvVarIdentityResolver

    monkeypatch.setenv("SORABEL_PROFILE", "admin")
    with pytest.raises(ValueError, match="profil inconnu"):
        EnvVarIdentityResolver().resolve()
```

Run : `uv run pytest tests/unit/test_identity.py -v`
Attendu : `ModuleNotFoundError: No module named 'mcp_server.identity'`

- [ ] **Step 2 : Implémenter `mcp_server/identity.py`**

```python
"""Résolution du profil appelant — jamais un paramètre de tool (E4).

Pour la démo, le contrat d'intégration (``tests/conftest.py``, ``README.md``) résout le
profil depuis la variable d'environnement ``SORABEL_PROFILE``, lue une fois par process
serveur (un process par client interne). ``IdentityResolver`` est un ``Protocol`` — même
patron que ``AccessRules``/``TraceRecorder`` — pour qu'un vrai IdP (OAuth 2.0/OIDC, voir
conception/3_MCP/questions_reponses_mcp.md § 1.5) soit substituable plus tard sans
modifier ``mcp_server/server.py`` (spec_mcp.md § 4.2). Non implémenté ici : hors
périmètre de ce chantier, non exigé par le brief ni par le contrat de test fourni.
"""

from __future__ import annotations

import os
from typing import Protocol

from sql.access import PROFILES

DEFAULT_PROFILE = "support"


class IdentityResolver(Protocol):
    def resolve(self) -> str: ...


class EnvVarIdentityResolver:
    """Lit ``SORABEL_PROFILE`` (défaut ``support``) — un process serveur par client."""

    def resolve(self) -> str:
        profile = os.environ.get("SORABEL_PROFILE", DEFAULT_PROFILE)
        if profile not in PROFILES:
            raise ValueError(f"profil inconnu : {profile!r}")
        return profile
```

- [ ] **Step 3 : Lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_identity.py -v`
Attendu : 3 PASS.

- [ ] **Step 4 : Lint et types**

Run : `uv run ruff check mcp_server tests/unit/test_identity.py && uv run mypy mcp_server`

- [ ] **Step 5 : Commit**

```bash
git add mcp_server/identity.py tests/unit/test_identity.py
git commit -m "feat(mcp_server): resolve the caller profile from SORABEL_PROFILE"
```

---

## Task 4 : Enveloppe commune (`mcp_server/envelope.py`)

**Files :**
- Create : `mcp_server/envelope.py`
- Test : `tests/unit/test_envelope.py`

**Interfaces :**
- Consomme : `retrieval.corpus.IndexedChunk`, `retrieval.engine.Hit`,
  `retrieval.engine.SearchDocsResponse`, `retrieval.engine.ListSourcesResponse`,
  `sql.schema.SchemaResponse`, `sql.engine.AskDatabaseResult`,
  `sql.tools.CheckStockResult`, `sql.tools.OrderStatusResult`.
- Produit : `Envelope` (dataclass : `status`, `payload`, `message`, `error_code`, méthode
  `to_call_tool_result() -> types.CallToolResult`), et une fonction `*_envelope()` par
  tool : `answer_question_envelope`, `search_docs_envelope`, `get_document_envelope`,
  `list_sources_envelope`, `get_schema_envelope`, `ask_database_envelope`,
  `check_stock_envelope`, `order_status_envelope`. Consommées par `mcp_server/server.py`
  (Task 6).

- [ ] **Step 1 : Écrire les tests (échouent, le module n'existe pas)**

Créer `tests/unit/test_envelope.py` :

```python
import json

from retrieval.corpus import IndexedChunk
from retrieval.engine import Hit, ListSourcesResponse, SearchDocResult, SearchDocsResponse
from retrieval.engine import CurrentVersionSummary, SourceSummary
from sql.engine import AskDatabaseResult
from sql.schema import ColumnInfo, SchemaResponse, TableInfo
from sql.tools import CheckStockResult, OrderStatusResult, WarehouseStock


def _chunk(**over) -> IndexedChunk:
    base = dict(
        chunk_id="proc-retour-01-v1.0#0", document_id="proc-retour-01-v1.0",
        content="Contenu.", title="Retour produit", type_doc="procedure_sav",
        collection="procedures_sav", version="1.0", date="2024-08-15",
        source="sav/proc-retour-01-v1.0.html", family_id="proc-retour-01",
        diversification_group="proc-retour", ref_produit=None,
    )
    base.update(over)
    return IndexedChunk(**base)


def test_envelope_ok_isError_false_et_pas_de_meta():
    from mcp_server.envelope import Envelope

    result = Envelope("ok", {"x": 1}, "").to_call_tool_result()
    assert result.isError is False
    assert result.meta is None
    body = json.loads(result.content[0].text)
    assert body == {"status": "ok", "payload": {"x": 1}, "message": ""}


def test_envelope_refuse_isError_true_et_code_dans_meta():
    from mcp_server.envelope import Envelope

    result = Envelope("refused", {}, "non autorisé", "FORBIDDEN").to_call_tool_result()
    assert result.isError is True
    assert result.meta == {"sorabel/error_code": "FORBIDDEN"}


def test_envelope_rejette_un_code_hors_des_4_minimums():
    import pytest

    from mcp_server.envelope import Envelope

    with pytest.raises(ValueError, match="code d'erreur"):
        Envelope("refused", {}, "x", "VALIDATION")


def test_answer_question_hors_corpus():
    from mcp_server.envelope import answer_question_envelope

    env = answer_question_envelope(True, "pertinence insuffisante", "", [])
    assert env.status == "hors_corpus"
    assert env.error_code == "OUT_OF_CORPUS"
    assert env.message == "pertinence insuffisante"


def test_answer_question_ok_avec_reference_de_repli_si_pas_de_ref_produit():
    # E1 : chaque source cite titre + référence + date. Un document sans ref_produit
    # (procedure_sav, notes) utilise son document_id comme référence, pour ne jamais
    # renvoyer une référence vide (vérifié empiriquement sur le corpus SAV, spec_mcp.md).
    from mcp_server.envelope import answer_question_envelope

    hit = Hit(chunk=_chunk(), rerank_score=0.9)
    env = answer_question_envelope(False, None, "La réponse.", [hit])
    assert env.status == "ok"
    assert env.payload["answer"] == "La réponse."
    source = env.payload["sources"][0]
    assert source["titre"] == "Retour produit"
    assert source["reference"] == "proc-retour-01-v1.0"  # repli sur document_id
    assert source["date"] == "2024-08-15"


def test_answer_question_ok_avec_ref_produit_utilise_ref_produit():
    from mcp_server.envelope import answer_question_envelope

    hit = Hit(chunk=_chunk(ref_produit="REF-8842"), rerank_score=0.9)
    env = answer_question_envelope(False, None, "Réponse.", [hit])
    assert env.payload["sources"][0]["reference"] == "REF-8842"


def test_search_docs_envelope_porte_doc_type():
    from mcp_server.envelope import search_docs_envelope

    result = SearchDocResult(
        chunk_id="REF-8842#0", rank=1, title="Fiche REF-8842", ref_produit="REF-8842",
        version="2.1", date="2026-01-10", source="fiches/REF-8842.pdf", content="...",
        rrf_score=None, type_doc="fiche_technique",
    )
    env = search_docs_envelope(SearchDocsResponse(results=[result], query="x", retrieval_count=1))
    assert env.status == "ok"
    hit = env.payload["hits"][0]
    assert hit["metadata"]["reference"] == "REF-8842"
    assert hit["metadata"]["doc_type"] == "fiche_technique"


def test_get_document_envelope_absent():
    from mcp_server.envelope import get_document_envelope

    env = get_document_envelope(None)
    assert env.status == "error"


def test_get_document_envelope_present():
    from mcp_server.envelope import get_document_envelope

    env = get_document_envelope(_chunk())
    assert env.status == "ok"
    assert env.payload["text"] == "Contenu."
    assert env.payload["metadata"]["doc_type"] == "procedure_sav"


def test_list_sources_envelope():
    from mcp_server.envelope import list_sources_envelope

    source = SourceSummary(
        family_id="colis", current_version=CurrentVersionSummary(
            document_id="colis-v2.0", title="Colis abîmé", version="2.0",
            date="2025-01-01", chunk_count=1,
        ), older_versions=[], ref_produit=None, type_doc="procedure_sav",
        collection="procedures_sav",
    )
    env = list_sources_envelope(ListSourcesResponse(sources=[source], total_count=1, filters_applied={}))
    assert env.status == "ok"
    assert env.payload["sources"][0]["doc_type"] == "procedure_sav"
    assert env.payload["total_count"] == 1


def test_get_schema_envelope():
    from mcp_server.envelope import get_schema_envelope

    schema = SchemaResponse(
        tables=(TableInfo(name="produits", description="", columns=(
            ColumnInfo(name="ref", type="TEXT", description="Référence", values=None),
        )),),
        relations=("commandes.client_id -> clients.id",),
    )
    env = get_schema_envelope(schema)
    assert env.status == "ok"
    assert env.payload["tables"][0]["columns"][0]["name"] == "ref"
    assert env.payload["relations"] == ["commandes.client_id -> clients.id"]


def test_ask_database_envelope_ok_ne_porte_pas_le_sql():
    from mcp_server.envelope import ask_database_envelope

    result = AskDatabaseResult(
        status="ok", columns=("n",), rows=((3,),), row_count=1, truncated=False,
        message="", code=None, sql_genere="SELECT ...", sql_execute="SELECT ...",
    )
    env = ask_database_envelope(result)
    assert env.status == "ok"
    assert "sql" not in env.payload
    assert env.payload["rows"] == [[3]]


def test_ask_database_envelope_refuse_porte_le_code_minimum():
    from mcp_server.envelope import ask_database_envelope

    result = AskDatabaseResult(
        status="refused", columns=(), rows=(), row_count=0, truncated=False,
        message="Cette demande n'est pas autorisée.", code="FORBIDDEN",
        sql_genere="", sql_execute="",
    )
    env = ask_database_envelope(result)
    assert env.status == "refused"
    assert env.error_code == "FORBIDDEN"


def test_ask_database_envelope_refuse_code_hors_minimum_devient_none():
    # VALIDATION/TIMEOUT ne font pas partie des 4 codes minimums (conception § 4.2,
    # « point ouvert ») : le code MCP reste absent, le message reste explicite.
    from mcp_server.envelope import ask_database_envelope

    result = AskDatabaseResult(
        status="refused", columns=(), rows=(), row_count=0, truncated=False,
        message="La requête produite n'est pas une lecture valide.", code="VALIDATION",
        sql_genere="", sql_execute="",
    )
    env = ask_database_envelope(result)
    assert env.error_code is None
    assert env.status == "refused"


def test_check_stock_envelope():
    from mcp_server.envelope import check_stock_envelope

    result = CheckStockResult(
        ref="REF-8842", found=True, total_quantity=12,
        by_warehouse=(WarehouseStock(entrepot="LILLE", quantite=12),),
    )
    env = check_stock_envelope(result)
    assert env.status == "ok"
    assert env.payload["total_quantity"] == 12


def test_order_status_envelope_introuvable_reste_ok():
    # « commande introuvable » n'est pas une erreur (conception § 4.2) : le tool a
    # répondu correctement, found=False le dit.
    from mcp_server.envelope import order_status_envelope

    result = OrderStatusResult(
        order_id="CMD-2026-0042", found=False, status=None, date_commande=None,
        montant_ht=None,
    )
    env = order_status_envelope(result)
    assert env.status == "ok"
    assert env.payload["found"] is False
```

Run : `uv run pytest tests/unit/test_envelope.py -v`
Attendu : `ModuleNotFoundError: No module named 'mcp_server.envelope'`

- [ ] **Step 2 : Implémenter `mcp_server/envelope.py`**

```python
"""Enveloppe commune : état interne des moteurs -> contrat MCP + JSON du scaffold fourni.

Double enveloppe assumée (spec_mcp.md § 4.4) : ``CallToolResult.isError`` +
``_meta["sorabel/error_code"]`` portent le contrat MCP natif de la conception ; le
premier bloc ``content`` est un texte JSON ``{status, payload, message}``, vocabulaire
imposé par ``tests/conftest.py`` (``ok | refused | clarification | hors_corpus |
error``). Les deux sont toujours synchrones : ``isError`` est vrai si et seulement si
``status != "ok"``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import mcp.types as types

from retrieval.corpus import IndexedChunk
from retrieval.engine import Hit, ListSourcesResponse, SearchDocsResponse
from sql.engine import AskDatabaseResult
from sql.schema import SchemaResponse
from sql.tools import CheckStockResult, OrderStatusResult

#: Codes minimums de la conception (questions_reponses_mcp.md § 4.2). Un refus de
#: validation structurelle (ex. VALIDATION, TIMEOUT côté SQL) n'y figure pas — le code
#: MCP reste absent (None), le message métier reste explicite (« point ouvert » assumé).
ERROR_CODES = frozenset({"FORBIDDEN", "OUT_OF_CORPUS", "OUT_OF_SCHEMA", "AMBIGUOUS"})


@dataclass(frozen=True)
class Envelope:
    status: str
    payload: dict
    message: str
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.error_code is not None and self.error_code not in ERROR_CODES:
            raise ValueError(f"code d'erreur non reconnu : {self.error_code!r}")

    def to_call_tool_result(self) -> types.CallToolResult:
        body = {"status": self.status, "payload": self.payload, "message": self.message}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(body, ensure_ascii=False))],
            isError=self.status != "ok",
            meta={"sorabel/error_code": self.error_code} if self.error_code else None,
        )


def _citation(chunk: IndexedChunk) -> dict:
    """Titre + référence + date (E1). ``ref_produit`` est absent pour les collections
    sav/ et notes/ (spec § 2.5 du chantier RAG) : le document_id sert alors de
    référence, pour ne jamais renvoyer une citation sans référence."""
    return {
        "titre": chunk.title,
        "reference": chunk.ref_produit or chunk.document_id,
        "date": chunk.date,
    }


def answer_question_envelope(
    is_refusal: bool, reason: str | None, answer: str, hits: list[Hit]
) -> Envelope:
    if is_refusal:
        return Envelope("hors_corpus", {}, reason or "Aucune source pertinente.", "OUT_OF_CORPUS")
    sources = [_citation(hit.chunk) for hit in hits]
    return Envelope("ok", {"answer": answer, "sources": sources}, "")


def search_docs_envelope(response: SearchDocsResponse) -> Envelope:
    hits = [
        {
            "doc_id": r.chunk_id,
            "score": r.rrf_score,
            "text": r.content,
            "metadata": {
                "reference": r.ref_produit or "",
                "doc_type": r.type_doc,
                "version": r.version,
                "date": r.date,
            },
        }
        for r in response.results
    ]
    return Envelope("ok", {"hits": hits}, "")


def get_document_envelope(chunk: IndexedChunk | None) -> Envelope:
    if chunk is None:
        return Envelope("error", {}, "Document introuvable.")
    metadata = {
        "doc_id": chunk.document_id, "titre": chunk.title,
        "reference": chunk.ref_produit or "", "version": chunk.version, "date": chunk.date,
        "doc_type": chunk.type_doc, "collection": chunk.collection, "source": chunk.source,
    }
    return Envelope("ok", {"text": chunk.content, "metadata": metadata}, "")


def list_sources_envelope(response: ListSourcesResponse) -> Envelope:
    sources = [
        {
            "doc_id": s.current_version.document_id, "titre": s.current_version.title,
            "reference": s.ref_produit or "", "version": s.current_version.version,
            "date": s.current_version.date, "doc_type": s.type_doc,
        }
        for s in response.sources
    ]
    return Envelope("ok", {"sources": sources, "total_count": response.total_count}, "")


def get_schema_envelope(schema: SchemaResponse) -> Envelope:
    tables = [
        {
            "name": t.name, "description": t.description,
            "columns": [
                {"name": c.name, "type": c.type, "description": c.description,
                 "values": list(c.values) if c.values else []}
                for c in t.columns
            ],
        }
        for t in schema.tables
    ]
    return Envelope("ok", {"tables": tables, "relations": list(schema.relations)}, "")


def ask_database_envelope(result: AskDatabaseResult) -> Envelope:
    # Décision de cette session (spec_mcp.md § 4.1) : le SQL généré/exécuté n'est
    # jamais recopié dans le payload client, malgré le brief et le test fourni — seul
    # le journal le porte (déjà fait par sql/engine.py._record).
    if result.status == "ok":
        payload = {
            "columns": list(result.columns), "rows": [list(row) for row in result.rows],
            "row_count": result.row_count, "truncated": result.truncated,
        }
        return Envelope("ok", payload, "")
    code = result.code if result.code in ERROR_CODES else None
    return Envelope(result.status, {"rows": []}, result.message, code)


def check_stock_envelope(result: CheckStockResult) -> Envelope:
    payload = {
        "ref": result.ref, "found": result.found, "total_quantity": result.total_quantity,
        "by_warehouse": [{"entrepot": w.entrepot, "quantite": w.quantite}
                          for w in result.by_warehouse],
    }
    return Envelope("ok", payload, "")


def order_status_envelope(result: OrderStatusResult) -> Envelope:
    payload = {
        "order_id": result.order_id, "found": result.found, "status": result.status,
        "date_commande": result.date_commande, "montant_ht": result.montant_ht,
    }
    return Envelope("ok", payload, "")
```

- [ ] **Step 3 : Lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_envelope.py -v`
Attendu : tous PASS.

- [ ] **Step 4 : Lint et types**

Run : `uv run ruff check mcp_server tests/unit/test_envelope.py && uv run mypy mcp_server`

- [ ] **Step 5 : Commit**

```bash
git add mcp_server/envelope.py tests/unit/test_envelope.py
git commit -m "feat(mcp_server): map engine results to the MCP + JSON envelope"
```

---

## Task 5 : Catalogue des 8 tools (`mcp_server/catalogue.py`)

**Files :**
- Create : `mcp_server/catalogue.py`
- Test : `tests/unit/test_catalogue.py`

**Interfaces :**
- Consomme : `mcp_server.access.YamlAccessRules` (Task 2).
- Produit : `TOOL_NAMES: tuple[str, ...]` (les 8, dans l'ordre du brief),
  `SERVER_INSTRUCTIONS: str`, `build_tools(access_rules) -> list[types.Tool]`.
  Consommé par `mcp_server/server.py` (Task 6).

- [ ] **Step 1 : Écrire le test (échoue, le module n'existe pas)**

Créer `tests/unit/test_catalogue.py` :

```python
def test_les_8_tools_du_brief_sont_presents():
    from mcp_server.catalogue import TOOL_NAMES

    assert set(TOOL_NAMES) == {
        "answer_question", "search_docs", "get_document", "list_sources",
        "ask_database", "get_schema", "check_stock", "order_status",
    }


def test_chaque_tool_a_une_description_et_un_schema_objet():
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix
    from mcp_server.catalogue import build_tools

    tools = build_tools(YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH)))
    assert len(tools) == 8
    for tool in tools:
        assert tool.description and tool.description.strip()
        assert tool.inputSchema["type"] == "object"


def test_les_entrees_obligatoires_du_brief_sont_requises():
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix
    from mcp_server.catalogue import build_tools

    tools = {t.name: t for t in build_tools(YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH)))}
    assert tools["ask_database"].inputSchema["required"] == ["question"]
    assert tools["check_stock"].inputSchema["required"] == ["ref"]
    assert tools["order_status"].inputSchema["required"] == ["order_id"]
    assert tools["get_document"].inputSchema["required"] == ["document_id"]
    assert "required" not in tools["get_schema"].inputSchema
    assert "required" not in tools["list_sources"].inputSchema


def test_meta_roles_genere_depuis_la_matrice_reelle():
    # Aujourd'hui : les deux profils ont les mêmes tools (spec_mcp.md § 2, point 1/4).
    from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix
    from mcp_server.catalogue import build_tools

    tools = build_tools(YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH)))
    for tool in tools:
        assert tool.meta == {"sorabel/roles": ["commercial", "support"]}


def test_meta_roles_reflete_une_restriction_de_tool_si_elle_existe():
    # Vérifie que build_tools lit vraiment la matrice (pas une valeur figée) : avec une
    # matrice où un tool est réservé à un profil, _meta le reflète.
    from mcp_server.access import ProfileRules, YamlAccessRules
    from mcp_server.catalogue import build_tools

    matrix = {
        "support": ProfileRules(tools=frozenset({"check_stock"}), rag_collections=frozenset(),
                                 hidden_columns=frozenset()),
        "commercial": ProfileRules(tools=frozenset({"check_stock", "get_schema"}),
                                    rag_collections=frozenset(), hidden_columns=frozenset()),
    }
    tools = {t.name: t for t in build_tools(YamlAccessRules(matrix))}
    assert tools["get_schema"].meta == {"sorabel/roles": ["commercial"]}
    assert tools["check_stock"].meta == {"sorabel/roles": ["commercial", "support"]}
```

Run : `uv run pytest tests/unit/test_catalogue.py -v`
Attendu : `ModuleNotFoundError: No module named 'mcp_server.catalogue'`

- [ ] **Step 2 : Implémenter `mcp_server/catalogue.py`**

```python
"""Catalogue des 8 tools MCP — noms, schémas d'entrée, descriptions orientées choix.

Descriptions et Server Instructions traduisent
``conception/commun/catalogue_tools_mcp.md`` et
``conception/3_MCP/questions_reponses_mcp.md`` § 3 : elles disent au host QUAND
utiliser chaque tool, jamais QUI est autorisé (ça, c'est ``access.py`` + le contrôle
RBAC, conception § 2.2). ``_meta["sorabel/roles"]`` est généré depuis la matrice,
jamais maintenu à la main (conception § 2.4).
"""

from __future__ import annotations

import mcp.types as types

from mcp_server.access import YamlAccessRules

SERVER_INSTRUCTIONS = """\
Sorabel exposes documentary RAG tools and structured-data SQL tools.

Routing rules:

1. For documentary questions, use the RAG tools.
2. For structured business data, use the SQL tools.
3. Prefer a specialized deterministic tool whenever one covers the request:
   - product stock -> check_stock
   - order status -> order_status
4. Use ask_database only when no specialized SQL tool covers the request.
5. For an exact documentary product reference, prefer:
   list_sources -> get_document.
6. Use answer_question for general documentary questions.
"""

TOOL_NAMES = (
    "answer_question", "search_docs", "get_document", "list_sources",
    "ask_database", "get_schema", "check_stock", "order_status",
)

_DESCRIPTIONS: dict[str, str] = {
    "answer_question": (
        "Question documentaire générale nécessitant le pipeline RAG complet "
        "(recherche hybride + décision de couverture + réponse rédigée et sourcée). "
        "Ne pas utiliser pour explorer sans générer (préférer search_docs) ni pour "
        "une référence produit exacte déjà connue (préférer list_sources -> get_document)."
    ),
    "search_docs": (
        "Recherche documentaire brute (dense + BM25 + fusion), sans décision de "
        "couverture ni réponse rédigée — exploration ou diagnostic du retrieval. "
        "Ne pas privilégier par défaut pour une réponse documentaire complète."
    ),
    "get_document": (
        "Récupère un document complet à partir d'un document_id déjà connu (par "
        "exemple via list_sources). Ne relance pas de recherche approximative."
    ),
    "list_sources": (
        "Liste ou identifie des sources par métadonnées (collection, type de "
        "document, référence produit). À privilégier pour résoudre une référence "
        "documentaire exacte (REF-xxxx) vers un document_id, avant get_document."
    ),
    "ask_database": (
        "Question métier en langage naturel sur les données structurées (produits, "
        "stocks, commandes, clients, ventes), quand aucun tool SQL spécialisé ne "
        "couvre directement le besoin. Ne pas utiliser pour le stock d'une référence "
        "précise (préférer check_stock) ni pour le statut d'une commande identifiée "
        "(préférer order_status)."
    ),
    "get_schema": (
        "Schéma SQL accessible au profil courant, lu à la source. Sert à connaître "
        "le périmètre réellement accessible avant d'écrire une question métier."
    ),
    "check_stock": (
        "Stock d'une référence produit précise, SQL figé et déterministe, sans LLM. "
        "À utiliser en priorité sur ask_database pour toute question de stock portant "
        "sur une référence connue."
    ),
    "order_status": (
        "Statut, date et montant d'une commande identifiée, SQL figé et "
        "déterministe, sans LLM. À utiliser en priorité sur ask_database pour toute "
        "question portant uniquement sur le statut d'une commande."
    ),
}

_INPUT_SCHEMAS: dict[str, dict] = {
    "answer_question": {
        "type": "object",
        "properties": {"question": {"type": "string"}, "top_k": {"type": "integer", "default": 5}},
        "required": ["question"],
    },
    "search_docs": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "default": 5},
            "include_score": {"type": "boolean", "default": False},
        },
        "required": ["query"],
    },
    "get_document": {
        "type": "object",
        "properties": {"document_id": {"type": "string"}},
        "required": ["document_id"],
    },
    "list_sources": {
        "type": "object",
        "properties": {
            "collection": {"type": "string"},
            "type_doc": {"type": "string"},
            "ref_produit": {"type": "string"},
            "include_versions": {"type": "boolean", "default": False},
        },
    },
    "ask_database": {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
    "get_schema": {"type": "object", "properties": {}},
    "check_stock": {
        "type": "object",
        "properties": {"ref": {"type": "string"}},
        "required": ["ref"],
    },
    "order_status": {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    },
}


def build_tools(access_rules: YamlAccessRules) -> list[types.Tool]:
    """Construit le catalogue, avec ``_meta["sorabel/roles"]`` dérivé de la matrice."""
    tools = []
    for name in TOOL_NAMES:
        roles = sorted(
            profile for profile in ("commercial", "support")
            if name in access_rules.allowed_tools(profile)
        )
        tools.append(
            types.Tool(
                name=name,
                description=_DESCRIPTIONS[name],
                inputSchema=_INPUT_SCHEMAS[name],
                meta={"sorabel/roles": roles},
            )
        )
    return tools
```

- [ ] **Step 3 : Lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_catalogue.py -v`
Attendu : tous PASS.

- [ ] **Step 4 : Lint et types**

Run : `uv run ruff check mcp_server tests/unit/test_catalogue.py && uv run mypy mcp_server`

- [ ] **Step 5 : Commit**

```bash
git add mcp_server/catalogue.py tests/unit/test_catalogue.py
git commit -m "feat(mcp_server): declare the 8-tool catalogue and server instructions"
```

---

## Task 6 : Serveur MCP (`mcp_server/server.py`)

**Files :**
- Create : `mcp_server/server.py`
- Modify : `.env.example` (`GATEWAY_JOURNAL`), `Makefile` (cible `journal`)
- Test : `tests/integration/test_mcp_server.py`

**Interfaces :**
- Consomme : tout ce qui précède (Tasks 2-5), plus `retrieval.engine.SearchEngine`,
  `retrieval.answer.compose_answer`, `retrieval.reranker.AzureCohereReranker`,
  `gateway.embedder.AzureEmbedder`, `gateway.chroma.{chroma_client,open_collection}`,
  `sql.engine.SqlEngine`, `sql.trace.JsonlTraceRecorder`.
- Produit : `dispatch(name, arguments, *, profile, search_engine, sql_engine,
  llm_client, settings, trace) -> types.CallToolResult` (logique pure, testée sans
  stdio) ; `main()` (point d'entrée `python -m mcp_server.server`).

**Prérequis pour lancer les tests de cette tâche** : `make seed` (base réelle) doit
avoir tourné. Aucun réseau : le client LLM est un double (`FixedLLM`, même patron que
`tests/integration/test_sql_engine.py`), Chroma est éphémère avec un embedder factice
(même patron que `tests/integration/test_retrieval.py`).

- [ ] **Step 1 : Écrire le test d'intégration (échoue, le module n'existe pas)**

Créer `tests/integration/test_mcp_server.py` :

```python
"""Intégration : dispatch() contre de vrais moteurs, sans protocole stdio.

Le protocole MCP réel (list_tools/call_tool sur stdio) est déjà vérifié par la suite
d'acceptance (Task 7) — ce test-ci vérifie l'assemblage moteurs + enveloppe + journal,
plus vite et sans sous-processus.
"""

import json
from pathlib import Path

import chromadb
import pytest

from gateway.chroma import open_collection
from gateway.settings import get_settings
from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix
from mcp_server.server import dispatch
from retrieval.engine import SearchEngine
from retrieval.tokenize import tokenize
from sql.engine import SqlEngine
from sql.trace import JsonlTraceRecorder

DB_PATH = Path("data/sorabel.db")
pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="data/sorabel.db absente — lancer `make seed`"
)


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
    """Un client factice : les tools testés ici n'ont pas besoin d'un vrai LLM
    (check_stock/order_status sont figés, get_schema n'appelle pas le modèle, et le
    refus d'écriture est détecté avant tout appel — sql/generate.py:looks_like_write)."""

    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                raise AssertionError("le LLM ne doit pas être appelé pour ces tools")


@pytest.fixture(scope="module")
def search_engine():
    client = chromadb.EphemeralClient()
    collection = open_collection(client, "mcp_server_integration_test")
    collection.upsert(
        ids=["REF-8842#0"],
        documents=["Disjoncteur triphasé 32A, REF-8842."],
        embeddings=FakeEmbedder().embed(["Disjoncteur triphasé 32A, REF-8842."]),
        metadatas=[{
            "document_id": "REF-8842", "title": "Disjoncteur REF-8842",
            "type_doc": "fiche_technique", "collection": "fiches_techniques",
            "version": "2.1", "date": "2026-01-10", "source": "fiches/REF-8842.pdf",
            "family_id": "REF-8842", "diversification_group": "REF-8842",
            "ref_produit": "REF-8842",
        }],
    )
    return SearchEngine(collection, FakeEmbedder(), get_settings(), reranker=None)


@pytest.fixture()
def access_rules():
    return YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))


def _sql_engine(profile, access_rules, tmp_path) -> SqlEngine:
    trace = JsonlTraceRecorder(tmp_path / "journal.jsonl", tmp_path / "alertes.jsonl")
    return SqlEngine(profile, access_rules, trace, FixedLLM(), get_settings())


async def _call(name, arguments, *, profile, search_engine, sql_engine, trace):
    return await dispatch(
        name, arguments, profile=profile, search_engine=search_engine,
        sql_engine=sql_engine, llm_client=FixedLLM(), settings=get_settings(), trace=trace,
    )


async def test_check_stock_ok_sans_appel_llm(search_engine, access_rules, tmp_path):
    trace = JsonlTraceRecorder(tmp_path / "journal.jsonl", tmp_path / "alertes.jsonl")
    sql_engine = _sql_engine("commercial", access_rules, tmp_path)
    result = await _call(
        "check_stock", {"ref": "REF-8842"}, profile="commercial",
        search_engine=search_engine, sql_engine=sql_engine, trace=trace,
    )
    body = json.loads(result.content[0].text)
    assert result.isError is False
    assert body["status"] == "ok"
    assert body["payload"]["ref"] == "REF-8842"


async def test_get_schema_filtre_les_colonnes_sensibles_pour_support(
    search_engine, access_rules, tmp_path
):
    trace = JsonlTraceRecorder(tmp_path / "journal.jsonl", tmp_path / "alertes.jsonl")
    sql_engine = _sql_engine("support", access_rules, tmp_path)
    result = await _call(
        "get_schema", {}, profile="support", search_engine=search_engine,
        sql_engine=sql_engine, trace=trace,
    )
    body = json.loads(result.content[0].text)
    assert body["status"] == "ok"  # accessible, seul le contenu est filtré
    colonnes = {
        (t["name"], c["name"]) for t in body["payload"]["tables"] for c in t["columns"]
    }
    assert ("produits", "prix_achat_ht") not in colonnes
    assert ("produits", "marge_pct") not in colonnes
    assert ("ventes", "marge_ht") not in colonnes


async def test_ask_database_refuse_une_tentative_d_ecriture_et_journalise(
    search_engine, access_rules, tmp_path
):
    journal = tmp_path / "journal.jsonl"
    trace = JsonlTraceRecorder(journal, tmp_path / "alertes.jsonl")
    sql_engine = _sql_engine("commercial", access_rules, tmp_path)
    result = await _call(
        "ask_database", {"question": "supprime les commandes de test"},
        profile="commercial", search_engine=search_engine, sql_engine=sql_engine,
        trace=trace,
    )
    body = json.loads(result.content[0].text)
    assert result.isError is True
    assert body["status"] == "refused"
    assert "sql" not in body["payload"]  # spec_mcp.md § 4.1
    entries = [json.loads(l) for l in journal.read_text("utf-8").splitlines()]
    assert any(e["tool"] == "ask_database" and e["statut"] == "refused" for e in entries)


async def test_search_docs_trouve_par_reference_exacte_et_journalise(
    search_engine, access_rules, tmp_path
):
    journal = tmp_path / "journal.jsonl"
    trace = JsonlTraceRecorder(journal, tmp_path / "alertes.jsonl")
    sql_engine = _sql_engine("support", access_rules, tmp_path)
    result = await _call(
        "search_docs", {"query": "REF-8842"}, profile="support",
        search_engine=search_engine, sql_engine=sql_engine, trace=trace,
    )
    body = json.loads(result.content[0].text)
    assert body["payload"]["hits"][0]["metadata"]["reference"] == "REF-8842"
    entries = [json.loads(l) for l in journal.read_text("utf-8").splitlines()]
    assert entries and entries[0]["tool"] == "search_docs" and entries[0]["profil"] == "support"
```

Run : `uv run pytest tests/integration/test_mcp_server.py -v`
Attendu : `ModuleNotFoundError: No module named 'mcp_server.server'`

- [ ] **Step 2 : Implémenter `mcp_server/server.py`**

```python
"""Point d'entrée du serveur MCP — assemble matrice, identité, moteurs, catalogue,
journal et enveloppe pour exposer les 8 tools déjà conçus (chantiers 1 et 2).

``python -m mcp_server.server`` : transport stdio, un process par profil (résolu une
seule fois au démarrage, jamais un paramètre de tool — E4). ``dispatch()`` est la
logique pure (nom de tool + arguments + moteurs -> CallToolResult), testée sans
protocole MCP réel dans tests/integration/test_mcp_server.py ; ``main()`` la branche
au SDK MCP et au transport stdio.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import mcp.types as types
from langfuse import Langfuse
from langfuse.openai import OpenAI
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from gateway.chroma import chroma_client, open_collection
from gateway.embedder import AzureEmbedder
from gateway.settings import Settings, get_settings
from mcp_server.access import DEFAULT_MATRIX_PATH, YamlAccessRules, load_matrix
from mcp_server.catalogue import SERVER_INSTRUCTIONS, build_tools
from mcp_server.envelope import (
    Envelope,
    answer_question_envelope,
    ask_database_envelope,
    check_stock_envelope,
    get_document_envelope,
    get_schema_envelope,
    list_sources_envelope,
    order_status_envelope,
    search_docs_envelope,
)
from mcp_server.identity import EnvVarIdentityResolver
from retrieval.answer import compose_answer
from retrieval.engine import SearchEngine
from retrieval.reranker import AzureCohereReranker
from sql.engine import SqlEngine
from sql.trace import JsonlTraceRecorder, TraceRecorder

#: Journal unique (conception/commun/journal_mcp.md), surchargeable par le contrat
#: d'intégration fourni (tests/conftest.py : GATEWAY_JOURNAL).
DEFAULT_JOURNAL_PATH = Path("logs/mcp_audit.jsonl")

#: Tools RAG sans journalisation propre côté moteur (contrairement aux 4 tools SQL,
#: qui s'auto-journalisent déjà dans sql/engine.py._record — spec_mcp.md § 4.5).
_RAG_TOOLS = frozenset({"answer_question", "search_docs", "list_sources", "get_document"})


def build_search_engine(settings: Settings) -> SearchEngine:
    collection = open_collection(chroma_client(settings), settings.chroma_collection)
    reranker = AzureCohereReranker(settings) if settings.rerank_enabled else None
    return SearchEngine(collection, AzureEmbedder(settings), settings, reranker=reranker)


async def dispatch(
    name: str,
    arguments: dict,
    *,
    profile: str,
    search_engine: SearchEngine,
    sql_engine: SqlEngine,
    llm_client: Any,
    settings: Settings,
    trace: TraceRecorder,
) -> types.CallToolResult:
    """Exécute un tool déjà résolu (profil, moteurs) et journalise les tools RAG.

    Les 4 tools SQL journalisent déjà eux-mêmes (``SqlEngine._record``) — ne pas
    dupliquer l'écriture ici, sous peine de deux entrées par appel SQL.
    """
    envelope = await _run(
        name, arguments, search_engine=search_engine, sql_engine=sql_engine,
        llm_client=llm_client, settings=settings,
    )
    if name in _RAG_TOOLS:
        trace.record({
            "profil": profile,
            "tool": name,
            "question": arguments.get("question") or arguments.get("query"),
            "statut": envelope.status,
            "code": envelope.error_code,
            "detail": envelope.message,
        })
    return envelope.to_call_tool_result()


async def _run(
    name: str, arguments: dict, *, search_engine: SearchEngine, sql_engine: SqlEngine,
    llm_client: Any, settings: Settings,
) -> Envelope:
    if name == "answer_question":
        outcome = search_engine.search(arguments["question"], arguments.get("top_k"))
        answer = "" if outcome.is_refusal else compose_answer(
            llm_client, settings.azure_model_text_generation, arguments["question"], outcome.hits
        )
        return answer_question_envelope(outcome.is_refusal, outcome.reason, answer, outcome.hits)
    if name == "search_docs":
        response = search_engine.search_docs(
            arguments["query"], arguments.get("top_k", 5), arguments.get("include_score", False)
        )
        return search_docs_envelope(response)
    if name == "get_document":
        return get_document_envelope(search_engine.get_document(arguments["document_id"]))
    if name == "list_sources":
        response = search_engine.list_sources(
            arguments.get("collection"), arguments.get("type_doc"),
            arguments.get("ref_produit"), arguments.get("include_versions", False),
        )
        return list_sources_envelope(response)
    if name == "get_schema":
        return get_schema_envelope(sql_engine.get_schema())
    if name == "ask_database":
        return ask_database_envelope(sql_engine.ask_database(arguments["question"]))
    if name == "check_stock":
        return check_stock_envelope(sql_engine.check_stock(arguments["ref"]))
    if name == "order_status":
        return order_status_envelope(sql_engine.order_status(arguments["order_id"]))
    raise ValueError(f"tool inconnu : {name!r}")  # jamais atteint : le catalogue borne les noms


async def main() -> None:
    settings = get_settings()
    profile = EnvVarIdentityResolver().resolve()
    access_rules = YamlAccessRules(load_matrix(DEFAULT_MATRIX_PATH))
    journal_path = Path(os.environ.get("GATEWAY_JOURNAL", str(DEFAULT_JOURNAL_PATH)))
    trace = JsonlTraceRecorder(journal_path, settings.sql_alert_log)

    Langfuse(
        public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_base_url,
    )
    llm_client = OpenAI(base_url=settings.azure_ai_endpoint, api_key=settings.azure_ai_api_key)

    search_engine = build_search_engine(settings)
    sql_engine = SqlEngine(profile, access_rules, trace, llm_client, settings)

    server = Server("sorabel-data-gateway", instructions=SERVER_INSTRUCTIONS)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return build_tools(access_rules)

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> types.CallToolResult:
        return await dispatch(
            name, arguments, profile=profile, search_engine=search_engine,
            sql_engine=sql_engine, llm_client=llm_client, settings=settings, trace=trace,
        )

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3 : Corriger le chemin de journal dans la config d'exemple**

Dans `.env.example`, remplacer :

```
GATEWAY_JOURNAL=logs/journal.jsonl
```

par :

```
GATEWAY_JOURNAL=logs/mcp_audit.jsonl
```

- [ ] **Step 4 : Corriger la cible `journal` du Makefile**

Dans `Makefile`, cible `journal`, remplacer `logs/journal.jsonl` par `logs/mcp_audit.jsonl` :

```makefile
journal:
	@tail -n 20 logs/mcp_audit.jsonl 2>/dev/null || echo "journal vide"
```

- [ ] **Step 5 : Lancer le test d'intégration**

Prérequis : `make seed` si `data/sorabel.db` est absente.
Run : `uv run pytest tests/integration/test_mcp_server.py -v`
Attendu : 4 PASS. Si une divergence de clé JSON apparaît (ex. structure de
`by_warehouse`), corriger l'enveloppe (Task 4) ou le test, pas l'inverse sans relire
spec_mcp.md.

- [ ] **Step 6 : Lint et types**

Run : `uv run ruff check mcp_server tests/integration/test_mcp_server.py`
Run : `uv run mypy gateway ingest retrieval sql mcp_server`

- [ ] **Step 7 : Vérification manuelle en conditions réelles**

Nécessite `make up` (Chroma) et des credentials Azure valides dans `.env`.

```bash
make serve &
sleep 1
uv run python scripts/mcp_client.py --profile support --tool check_stock --args '{"ref": "REF-8842"}'
uv run python scripts/mcp_client.py --profile commercial --tool get_schema --args '{}'
kill %1
```

Attendu : deux réponses JSON `{"status": "ok", ...}`, sans exception au démarrage du
serveur (import circulaire, tool manquant, `KeyError` de description de colonne).

- [ ] **Step 8 : Commit**

```bash
git add mcp_server/server.py tests/integration/test_mcp_server.py .env.example Makefile
git commit -m "feat(mcp_server): wire the stdio MCP server over the RAG and SQL engines"
```

---

## Task 7 : Adapter la suite d'acceptance fournie

**Files :**
- Modify : `tests/conftest.py`
- Modify : `tests/acceptance/test_mcp.py`
- Modify : `tests/acceptance/test_sql.py`
- Ne pas modifier : `tests/acceptance/test_rag.py` (aucun écart, spec_mcp.md § 5)

Rappel du contexte (spec_mcp.md § 2 et § 5) : `tests/conftest.py` et une partie de
`tests/acceptance/*.py` encodent le contrat de `docs/cadrage_dsi.md`, document déclaré
écarté (`CHANGELOG.md`, 2026-09-01). Cette tâche aligne le scaffold sur la conception
réellement suivie, sans changer l'intention des tests (E1-E5 vérifiées en boîte noire).

- [ ] **Step 1 : Clés du journal en français dans `tests/conftest.py`**

Dans `tests/conftest.py`, la fonction `read_journal` ne change pas (elle décode du
JSON brut, agnostique aux clés) ; ce sont les *usages* dans `test_mcp.py`/`test_sql.py`
qu'il faut corriger (Steps 3-4). Dans `tests/conftest.py`, `TOOLS_BY_PROFILE` :

Remplacer :

```python
TOOLS_BY_PROFILE = {
    "support": {
        "answer_question", "search_docs", "get_document", "list_sources",
        "ask_database", "check_stock", "order_status",
    },
    "commercial": set(ALL_TOOLS),
}
```

par :

```python
# Aucun tool n'est interdit dans son intégralité à un profil (spec_mcp.md § 2, point
# 1/4 : contrairement à la matrice de docs/cadrage_dsi.md, écartée — voir
# CHANGELOG.md, 2026-09-01). La seule restriction réelle du système porte sur 3
# colonnes SQL (sql/access.py:SENSITIVE_COLUMNS), pas sur l'accès à un tool.
TOOLS_BY_PROFILE = {"support": set(ALL_TOOLS), "commercial": set(ALL_TOOLS)}
```

- [ ] **Step 2 : Vérifier `data/sorabel.db` et l'environnement de test**

```bash
test -f data/sorabel.db || make seed
```

- [ ] **Step 3 : Réécrire les deux tests de `test_mcp.py` qui présupposaient un refus de tool**

Dans `tests/acceptance/test_mcp.py`, remplacer les deux fonctions
`test_matrice_d_acces_respectee` et `test_refus_message_clair_et_journalise` par :

```python
async def test_get_schema_filtre_les_colonnes_sensibles_pour_support():
    # E4/E5 : get_schema est accessible aux deux profils (aucun tool n'est interdit
    # dans son intégralité, spec_mcp.md § 2/§ 4.3) — mais son contenu est filtré :
    # support ne voit jamais les 3 colonnes sensibles.
    async with gateway_session("support") as call:
        support_schema = await call("get_schema", {})
    async with gateway_session("commercial") as call:
        commercial_schema = await call("get_schema", {})

    assert support_schema["status"] == "ok"
    assert commercial_schema["status"] == "ok"

    def colonnes(schema: dict) -> set[tuple[str, str]]:
        return {
            (t["name"], c["name"])
            for t in schema["payload"]["tables"]
            for c in t["columns"]
        }

    support_colonnes = colonnes(support_schema)
    assert ("produits", "prix_achat_ht") not in support_colonnes
    assert ("produits", "marge_pct") not in support_colonnes
    assert ("ventes", "marge_ht") not in support_colonnes
    assert ("produits", "prix_achat_ht") in colonnes(commercial_schema)


async def test_refus_donnee_message_clair_et_journalise(journal_path):
    # E4 + E5 : un refus au niveau donnée (ici, colonne interdite via ask_database —
    # déjà le cas déterministe de test_sql.py::test_profil_support_jamais_de_marge)
    # est explicite et journalisé, même s'il n'existe aucun refus de tool entier.
    async with gateway_session("support", journal_path) as call:
        result = await call("ask_database", {"question": "quelle est la marge sur la REF-8842 ?"})
    assert result["status"] == "refused"
    assert result["message"].strip()

    entries = read_journal(journal_path)
    assert any(
        e["profil"] == "support" and e["tool"] == "ask_database" and e["statut"] == "refused"
        for e in entries
    )
```

Dans les deux tests restants de ce fichier
(`test_briques_du_rag_utilisables_separement`, `test_journal_exhaustif_autorises_et_refuses`),
remplacer les clés anglaises par les françaises :

```python
    # test_journal_exhaustif_autorises_et_refuses, dans les assertions sur `entries` :
    assert [e["tool"] for e in entries] == [tool for tool, _ in calls]
    statuses = {e["statut"] for e in entries}
    assert "refused" in statuses
    assert statuses - {"refused"}, "le journal doit aussi tracer les appels autorisés"
```

- [ ] **Step 4 : Retirer l'assertion sur le SQL renvoyé, dans `test_sql.py`**

Dans `tests/acceptance/test_sql.py`,
`test_ask_database_repond_et_montre_sa_requete`, remplacer :

```python
    assert result["status"] == "ok"
    assert "select" in result["payload"]["sql"].lower()
    assert result["payload"]["rows"][0][0] == attendu
```

par :

```python
    assert result["status"] == "ok"
    assert "sql" not in result["payload"]  # décision assumée, spec_mcp.md § 4.1
    assert result["payload"]["rows"][0][0] == attendu
```

Renommer la fonction en `test_ask_database_repond_sans_exposer_le_sql` (le nom
précédent décrivait un comportement qu'on ne démontre plus).

- [ ] **Step 5 : Lancer toute la suite d'acceptance**

Prérequis : `make seed`, `make up`, credentials Azure valides dans `.env`.

Run : `uv run pytest tests/acceptance -v`
Attendu : tous PASS. Si un test échoue sur une clé de payload non prévue par
`mcp_server/envelope.py` (Task 4), corriger l'enveloppe et rejouer — ne pas assouplir
le test sans relire `docs/spec_mcp.md`.

- [ ] **Step 6 : Lancer toute la suite (unit + intégration + acceptance) une dernière fois**

Run : `uv run pytest -q`
Attendu : 0 échec.

- [ ] **Step 7 : Commit**

```bash
git add tests/conftest.py tests/acceptance/test_mcp.py tests/acceptance/test_sql.py
git commit -m "test(acceptance): align the provided scaffold with the followed conception"
```

---

## Task 8 : Interface graphique (`app_gateway.py`)

**Files :**
- Create : `app_gateway.py`
- Modify : `Makefile` (cible `ui-gateway`)

**Interfaces :**
- Consomme : `scripts/mcp_client.py` (même mécanique de connexion stdio, pas de
  nouvelle abstraction), le vrai serveur `python -m mcp_server.server`.

Contrairement à `app.py`/`app_sql.py` (qui construisent `SearchEngine`/`SqlEngine`
directement), cette appli passe par un **vrai client MCP** — elle démontre la gateway
de bout en bout, sélecteur de profil compris (spec_mcp.md § 6).

- [ ] **Step 1 : Écrire `app_gateway.py`**

```python
"""Démo de la gateway complète — interface Streamlit passant par le vrai serveur MCP.

Contrairement à app.py/app_sql.py (qui instancient SearchEngine/SqlEngine
directement), cette appli est un vrai client MCP (mêmes primitives que
scripts/mcp_client.py) : elle relance une session stdio vers
`python -m mcp_server.server` à chaque changement de profil, exactement comme le
ferait un host MCP réel. Streamlit est synchrone : chaque appel ouvre et referme sa
propre session (asyncio.run), plus simple à raisonner qu'une session persistante
partagée entre les reruns du script (spec_mcp.md § 6).

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
    "get_document": {"document_id": ""},
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
```

- [ ] **Step 2 : Ajouter la cible Makefile**

Dans `Makefile`, après `ui-sql:`, ajouter :

```makefile
ui-gateway:
	uv run streamlit run app_gateway.py
```

Et dans l'en-tête `.PHONY:`, ajouter `ui-gateway`.

- [ ] **Step 3 : Vérification manuelle**

Nécessite `make up` (Chroma) et `.env` valide.

```bash
make ui-gateway
```

Dans le navigateur : sélectionner profil `support`, tool `get_schema`, cliquer
« Appeler » — vérifier que les 3 colonnes sensibles sont absentes de la réponse.
Basculer sur `commercial`, rejouer le même appel — vérifier qu'elles apparaissent.

- [ ] **Step 4 : Commit**

```bash
git add app_gateway.py Makefile
git commit -m "feat(ui): add a Streamlit demo driving the real MCP gateway"
```

---

## Task 9 : Documentation et clôture du chantier

**Files :**
- Modify : `sorabel_phase3/README.md` (racine du dépôt de conception)
- Modify : `sorabel_phase3/sorabel_phase3/README.md` (racine de l'implémentation)
- Modify : `CHANGELOG.md`, `TODO.md`, `MEMORY.md` (racine du dépôt de conception)

Aucun code dans cette tâche — mise à jour de la documentation de suivi, conformément
aux conventions du projet (`MEMORY.md` § « Conventions »).

- [ ] **Step 1 : `sorabel_phase3/sorabel_phase3/README.md`**

Retirer les mentions « (à construire) » des 4 puces de `## Features` désormais
construites, et dans `## Layout`, retirer « (à concevoir et construire) » pour
`mcp_server/`. Ajouter une ligne d'usage pour `app_gateway.py` dans `## Démarrage`.

- [ ] **Step 2 : `CHANGELOG.md` (racine du dépôt de conception)**

Ajouter en tête (plus récent en premier) une entrée datée du jour, résumant : serveur
MCP assemblé, matrice réduite aux 3 colonnes SQL (aucune restriction de tool ni de
collection RAG), enveloppe double (isError/_meta + JSON), SQL non exposé au client,
adaptation du scaffold de tests fourni (clés du journal, refus de tool remplacé par un
refus donnée), GUI `app_gateway.py`.

- [ ] **Step 3 : `TODO.md`**

Cocher les items du Chantier 3 devenus faits (catalogue de tools, application de la
matrice et journalisation, `scripts/mcp_client.py` — déjà existant —, interface
graphique).

- [ ] **Step 4 : `MEMORY.md`**

Mettre à jour le tableau « État d'avancement » : les trois chantiers (RAG, Text-to-SQL,
MCP) sont faits. Ajouter une ligne dans « Ce qu'il ne faut pas redécouvrir » sur la
matrice réduite (pas de restriction de tool/collection dans ce MVP) si cette
information n'y figure pas déjà.

- [ ] **Step 5 : Commit**

```bash
git add sorabel_phase3/README.md CHANGELOG.md TODO.md MEMORY.md
# (chemins relatifs au dépôt de conception ; README de sorabel_phase3/sorabel_phase3/
# est déjà dans le dépôt git de l'implémentation — commit séparé si besoin)
git commit -m "docs: close out chantier 3 (MCP gateway) in the tracking files"
```

---

## Self-review

**Couverture de la spec** : § 3 (architecture/modules) -> Tasks 2-6 ; § 4.1 (SQL non
exposé) -> Task 4/7 ; § 4.2 (IdP) -> Task 3 ; § 4.3 (pas de refus de tool) -> Tasks 2/7 ;
§ 4.4 (enveloppe double) -> Task 4 ; § 4.5 (journal) -> Task 6 ; § 5 (tests) -> Task 7 ;
§ 6 (GUI) -> Task 8 ; § 7 (niveaux de test) -> Tasks 2-4 (unitaire), 6 (intégration), 7
(acceptance). Aucun point de la spec sans tâche correspondante.

**Cohérence des types** : `Envelope` (Task 4) est le seul point de sortie de `_run()`
(Task 6) avant `to_call_tool_result()` — vérifié champ par champ contre les tests de
la Task 4. `YamlAccessRules` (Task 2) est le seul `AccessRules` injecté dans `SqlEngine`
(Task 6) et le seul consommé par `build_tools` (Task 5) — pas de deuxième
implémentation parallèle. `SearchDocResult.type_doc` (Task 1) est consommé une seule
fois, dans `search_docs_envelope` (Task 4).
