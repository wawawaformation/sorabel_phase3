# Plan d'implémentation — Ingestion du corpus

> **Pour un worker agentique :** SOUS-SKILL REQUIS : utiliser `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans` pour dérouler ce plan tâche par tâche. Les étapes sont en cases à cocher (`- [ ]`).

**Objectif :** transformer les 400 documents de `data/corpus/` en 400 chunks indexés dans Chroma, avec les métadonnées nécessaires aux citations (E1), à la recherche par référence exacte (E2) et au filtrage par profil (E4).

**Architecture :** un module par responsabilité dans `ingest/` — extraction par format, dérivation des métadonnées, assemblage du document canonique, chunking, embeddings, écriture Chroma —, orchestrés par `ingest/pipeline.py` et déclenchés par `scripts/ingest.py`. Les dépendances externes (client Chroma, calcul d'embeddings) sont **injectées**, jamais construites dans le pipeline : c'est ce qui permet de tester l'ingestion complète sans Docker ni appel réseau.

**Stack :** Python 3.11, `pypdf`, `beautifulsoup4`, `markdown`, `chromadb` 0.5, SDK `openai` (endpoint Azure AI Foundry), `pydantic` v2, `pydantic-settings`, `pytest` (`asyncio_mode = auto`), `ruff`, `mypy`.

**Spec de référence :** `docs/spec_ingestion.md`. Conception : `conception/1_RAG/questions_reponses_rag.md`.

## Contraintes globales

- Ligne max **100** caractères (`ruff`, `line-length = 100`), cible `py311`.
- `make lint` lance `mypy ingest retrieval sql mcp_server` : **tout le code de `ingest/` doit être annoté**.
- Chroma : une valeur de métadonnée est `str`, `int`, `float` ou `bool`. **`None` et les listes sont refusés** (`ValueError`). Une clé absente est la seule façon d'exprimer « pas de valeur ».
- Nom de collection Chroma : 3 à 63 caractères alphanumériques, `_` et `-` autorisés. Collection retenue : `sorabel_corpus`.
- **Aucune fonction d'embedding n'est configurée sur la collection Chroma** : les vecteurs sont toujours passés explicitement dans `embeddings=[...]`. Laisser Chroma calculer déclencherait le téléchargement de son modèle ONNX par défaut.
- La CI (`.github/workflows/quality.yml`) ne lance **ni `docker compose` ni de secret Azure** : aucun test ne doit exiger un serveur Chroma ni un appel réseau.
- Code en anglais, commentaires en français.
- `chunk_index` vaut toujours `0` (1 chunk = 1 document sur ce corpus). Aucun découpeur n'est implémenté.
- Les modèles `ingest/document.py` (`DocumentCanonique`) et `ingest/chunk.py` (`Chunk`) existent déjà et **ne doivent pas être modifiés** par ce plan.

## Chiffres de référence (mesurés sur `data/corpus/`)

| Grandeur | Valeur attendue |
|---|---|
| Fichiers | 400 (`fiches` 150, `notices` 80, `sav` 90, `notes` 80) |
| Chunks après ingestion | 400 |
| Chunks avec `ref_produit` | 230 (les PDF) |
| Chunks sans `ref_produit` | 170 (`sav` + `notes`) |
| `family_id` distincts | 350 |
| `diversification_group` `sav_*` | 10 |
| `diversification_group` `note_*` | 5 |

---

## Point à valider avant le chantier MCP (pas bloquant ici)

`tests/acceptance/` et `tests/conftest.py` référencent explicitement `docs/cadrage_dsi.md` — document que le formateur a demandé de considérer comme inexistant — et encodent son contrat (enveloppe `{status, payload, message}`, matrice où `support` n'a pas `get_schema`). **Ne rien y toucher dans ce plan** : l'ingestion n'y touche pas. À trancher avec le formateur avant d'implémenter les tools MCP, car ajuster une suite qui sert de barème demande son accord explicite.

---

## Structure de fichiers

| Fichier | Responsabilité |
|---|---|
| `ingest/settings.py` | configuration lue depuis l'environnement / `.env` |
| `ingest/errors.py` | `IngestionError` (échec fort, nomme fichier + champ) |
| `ingest/extract.py` | extraction texte + métadonnées brutes, une fonction par format |
| `ingest/metadata.py` | dérivation `document_id`, `collection`, `type_doc`, `family_id`, `diversification_group` |
| `ingest/build.py` | assemblage → `DocumentCanonique` |
| `ingest/chunking.py` | `DocumentCanonique` → `list[Chunk]` |
| `ingest/embedder.py` | protocole `Embedder`, implémentation Azure, texte à embedder |
| `ingest/store.py` | mapping métadonnées Chroma + `upsert` |
| `ingest/pipeline.py` | orchestration complète |
| `scripts/ingest.py` | point d'entrée CLI |
| `Makefile` | cible `ingest` |
| `tests/unit/…` | tests unitaires par module |
| `tests/integration/test_ingestion.py` | ingestion complète, Chroma éphémère + embedder factice |

---

## Task 1 : Configuration et erreurs

**Files:**
- Create: `ingest/settings.py`
- Create: `ingest/errors.py`
- Create: `tests/unit/__init__.py` (fichier vide)
- Test: `tests/unit/test_settings.py`

**Interfaces:**
- Consumes: rien.
- Produces: `Settings` (champs `corpus_dir: Path`, `chroma_url: str`, `chroma_collection: str`, `azure_ai_endpoint: str`, `azure_ai_api_key: str`, `azure_model_text_embedding_small: str`), `get_settings() -> Settings`, `IngestionError(Exception)`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_settings.py
from pathlib import Path

from ingest.settings import Settings


def test_valeurs_par_defaut():
    s = Settings(_env_file=None)
    assert s.corpus_dir == Path("data/corpus")
    assert s.chroma_collection == "sorabel_corpus"
    assert s.azure_model_text_embedding_small == "text-embedding-3-small"


def test_lecture_depuis_environnement(monkeypatch):
    monkeypatch.setenv("AZURE_AI_API_KEY", "cle-de-test")
    monkeypatch.setenv("CHROMA_URL", "http://ailleurs:9000")
    s = Settings(_env_file=None)
    assert s.azure_ai_api_key == "cle-de-test"
    assert s.chroma_url == "http://ailleurs:9000"


def test_variables_inconnues_ignorees(monkeypatch):
    # .env contient SORABEL_PROFILE, GATEWAY_JOURNAL, AZURE_MODEL_RERANKING…
    # qui ne sont pas des champs de Settings : ils ne doivent pas faire échouer.
    monkeypatch.setenv("SORABEL_PROFILE", "support")
    monkeypatch.setenv("AZURE_MODEL_RERANKING", "Cohere-rerank-v4.0-pro")
    Settings(_env_file=None)
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_settings.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'ingest.settings'`

- [ ] **Step 3 : écrire l'implémentation minimale**

```python
# ingest/settings.py
"""Configuration de l'ingestion, lue depuis l'environnement ou .env.

À déplacer dans un module partagé quand retrieval/ et mcp_server/ en auront besoin.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" : .env porte aussi des variables d'autres modules
    # (SORABEL_PROFILE, GATEWAY_JOURNAL, AZURE_MODEL_RERANKING…).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    corpus_dir: Path = Path("data/corpus")
    chroma_url: str = "http://localhost:8002"
    chroma_collection: str = "sorabel_corpus"
    azure_ai_endpoint: str = ""
    azure_ai_api_key: str = ""
    azure_model_text_embedding_small: str = "text-embedding-3-small"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

```python
# ingest/errors.py
"""Erreurs de l'ingestion."""


class IngestionError(Exception):
    """Champ obligatoire manquant ou corpus incohérent.

    L'ingestion échoue fort : le corpus est régulier sur ses 400 fichiers, donc une
    extraction qui échoue signale une hypothèse devenue fausse, pas un cas limite à
    contourner silencieusement.
    """

    def __init__(self, path: Path | str, detail: str) -> None:
        super().__init__(f"{path} : {detail}")
        self.path = str(path)
        self.detail = detail
```

Ajouter en tête de `ingest/errors.py` : `from pathlib import Path`.

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_settings.py -v`
Attendu : 3 PASS

- [ ] **Step 5 : commit**

```bash
git add ingest/settings.py ingest/errors.py tests/unit/__init__.py tests/unit/test_settings.py
git commit -m "feat(ingest): settings and IngestionError"
```

---

## Task 2 : Extraction PDF

**Files:**
- Create: `ingest/extract.py`
- Test: `tests/unit/test_extract_pdf.py`

**Interfaces:**
- Consumes: `IngestionError` (Task 1).
- Produces: `extract_pdf(path: Path) -> Extracted`, avec
  `Extracted = dataclass(text: str, title: str, version: str, date: str, ref_produit: str | None)`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_extract_pdf.py
from pathlib import Path

import pytest

from ingest.errors import IngestionError
from ingest.extract import extract_pdf

CORPUS = Path("data/corpus")


def test_fiche_technique():
    got = extract_pdf(CORPUS / "fiches" / "REF-1024-v2.1.pdf")
    assert got.title == "Disjoncteur triphasé 63 A courbe D"
    assert got.ref_produit == "REF-1024"
    assert got.version == "2.1"
    assert got.date == "2022-11-07"
    assert "Pouvoir de coupure" in got.text


def test_notice_meme_ligne():
    # Dans une notice, référence, version et date sont sur la MÊME ligne :
    # l'extraction ne doit pas dépendre de la position des lignes.
    got = extract_pdf(CORPUS / "notices" / "notice-REF-1459-v1.1.pdf")
    assert got.title == "Projecteur led 30 W rechargeable"
    assert got.ref_produit == "REF-1459"
    assert got.version == "1.1"
    assert got.date == "2024-03-23"


def test_champ_manquant_echoue(tmp_path):
    from pypdf import PdfWriter

    vide = tmp_path / "vide.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with vide.open("wb") as fh:
        writer.write(fh)

    with pytest.raises(IngestionError, match="title"):
        extract_pdf(vide)
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_extract_pdf.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'ingest.extract'`

- [ ] **Step 3 : écrire l'implémentation minimale**

```python
# ingest/extract.py
"""Extraction du texte et des métadonnées, une fonction par format du corpus.

Aucun LLM : les métadonnées sont structurées (regex sur les PDF, attributs pour le
HTML, front-matter pour le Markdown), jamais devinées.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from ingest.errors import IngestionError

RE_PDF_TITLE = re.compile(r"^(?:FICHE TECHNIQUE|NOTICE D'INSTALLATION)\s*-\s*(.+)$", re.M)
RE_PDF_REF = re.compile(r"R[ée]f[ée]rence produit\s*:\s*(REF-\d{4})")
RE_PDF_VERSION = re.compile(r"Version\s*:\s*([\d.]+)")
RE_PDF_DATE = re.compile(r"Date\s*:\s*(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Extracted:
    """Résultat brut d'une extraction, avant dérivation des identifiants."""

    text: str
    title: str
    version: str
    date: str
    ref_produit: str | None


def _require(pattern: re.Pattern[str], text: str, path: Path, field: str) -> str:
    match = pattern.search(text)
    if match is None:
        raise IngestionError(path, f"champ obligatoire introuvable : {field}")
    return match.group(1).strip()


def extract_pdf(path: Path) -> Extracted:
    """Extrait une fiche technique ou une notice.

    Les PDF du corpus ne portent aucune métadonnée embarquée : tout vient du texte.
    Les regex s'appliquent au texte entier, sans hypothèse de position de ligne — la
    mise en page diffère entre fiches et notices.
    """
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return Extracted(
        text=text.strip(),
        title=_require(RE_PDF_TITLE, text, path, "title"),
        version=_require(RE_PDF_VERSION, text, path, "version"),
        date=_require(RE_PDF_DATE, text, path, "date"),
        ref_produit=_require(RE_PDF_REF, text, path, "ref_produit"),
    )
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_extract_pdf.py -v`
Attendu : 3 PASS

- [ ] **Step 5 : commit**

```bash
git add ingest/extract.py tests/unit/test_extract_pdf.py
git commit -m "feat(ingest): PDF extraction (fiches, notices)"
```

---

## Task 3 : Extraction HTML

**Files:**
- Modify: `ingest/extract.py`
- Test: `tests/unit/test_extract_html.py`

**Interfaces:**
- Consumes: `Extracted`, `_require`, `IngestionError`.
- Produces: `extract_html(path: Path) -> Extracted` (`ref_produit` toujours `None`).

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_extract_html.py
from pathlib import Path

from ingest.extract import extract_html

CORPUS = Path("data/corpus")


def test_procedure_sav():
    got = extract_html(CORPUS / "sav" / "proc-casse-transport-01-v2.0.html")
    assert got.title == (
        "Procédure SAV — Colis reçu endommagé : constat et prise en charge (01)"
    )
    assert got.version == "2.0"
    assert got.date == "2026-04-05"
    # Les procédures SAV sont génériques : la référence citée n'est qu'un exemple.
    assert got.ref_produit is None
    assert "Conditions" in got.text
    assert "<" not in got.text  # balises retirées
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_extract_html.py -v`
Attendu : FAIL, `ImportError: cannot import name 'extract_html'`

- [ ] **Step 3 : écrire l'implémentation minimale**

Ajouter à `ingest/extract.py` :

```python
from bs4 import BeautifulSoup


def _meta_content(soup: BeautifulSoup, name: str, path: Path) -> str:
    tag = soup.find("meta", attrs={"name": name})
    if tag is None or not tag.get("content"):
        raise IngestionError(path, f"balise meta obligatoire introuvable : {name}")
    return str(tag["content"]).strip()


def extract_html(path: Path) -> Extracted:
    """Extrait une procédure SAV.

    Les 90 fichiers partagent la même séquence de balises et portent toujours les
    trois meta version/date/type. `ref_produit` est None par construction : ces
    procédures sont génériques.
    """
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    if soup.title is None or not soup.title.get_text(strip=True):
        raise IngestionError(path, "champ obligatoire introuvable : title")
    body = soup.body
    text = body.get_text(separator=" ", strip=True) if body is not None else ""
    return Extracted(
        text=text,
        title=soup.title.get_text(strip=True),
        version=_meta_content(soup, "version", path),
        date=_meta_content(soup, "date", path),
        ref_produit=None,
    )
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_extract_html.py -v`
Attendu : 1 PASS

- [ ] **Step 5 : commit**

```bash
git add ingest/extract.py tests/unit/test_extract_html.py
git commit -m "feat(ingest): HTML extraction (procédures SAV)"
```

---

## Task 4 : Extraction Markdown

**Files:**
- Modify: `ingest/extract.py`
- Test: `tests/unit/test_extract_markdown.py`

**Interfaces:**
- Consumes: `Extracted`, `IngestionError`.
- Produces: `extract_markdown(path: Path) -> Extracted`, `parse_front_matter(raw: str, path: Path) -> dict[str, str]`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_extract_markdown.py
from pathlib import Path

import pytest

from ingest.errors import IngestionError
from ingest.extract import extract_markdown, parse_front_matter

CORPUS = Path("data/corpus")


def test_note_interne():
    got = extract_markdown(CORPUS / "notes" / "note-2024-01-11-alerte-qualite-50.md")
    assert got.title == "Alerte qualité fournisseur"
    assert got.date == "2024-01-11"
    assert got.version == "1.0"  # '1.0' dans le fichier : guillemets retirés
    assert got.ref_produit is None
    assert "Filtech" in got.text
    assert "#" not in got.text  # syntaxe Markdown retirée


def test_front_matter_retire_les_guillemets():
    raw = "---\ntitre: T\nversion: '1.0'\ndate: 2024-01-11\n---\n\ncorps\n"
    assert parse_front_matter(raw, Path("x.md"))["version"] == "1.0"


def test_sans_front_matter_echoue(tmp_path):
    f = tmp_path / "sans.md"
    f.write_text("pas de front-matter\n", encoding="utf-8")
    with pytest.raises(IngestionError, match="front-matter"):
        extract_markdown(f)
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_extract_markdown.py -v`
Attendu : FAIL, `ImportError: cannot import name 'extract_markdown'`

- [ ] **Step 3 : écrire l'implémentation minimale**

Ajouter à `ingest/extract.py` :

```python
import markdown as markdown_lib

RE_FRONT_MATTER = re.compile(r"\A---\s*\n(?P<meta>.*?)\n---\s*\n(?P<body>.*)\Z", re.S)


def parse_front_matter(raw: str, path: Path) -> dict[str, str]:
    """Lit le front-matter `clé: valeur` et retire les guillemets encadrants.

    Les 80 notes portent exactement les mêmes 5 clés, avec des scalaires simples :
    un parseur minimal suffit, PyYAML n'apporterait rien.
    """
    match = RE_FRONT_MATTER.match(raw)
    if match is None:
        raise IngestionError(path, "front-matter absent ou mal délimité")
    fields: dict[str, str] = {}
    for line in match.group("meta").splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip("'\"")
    return fields


def extract_markdown(path: Path) -> Extracted:
    """Extrait une note interne : front-matter + corps réduit en texte brut.

    Le corps passe par Markdown → HTML → texte, donc par le même extracteur que le
    HTML de sav/ : un seul chemin de code d'extraction pour deux formats.
    """
    raw = path.read_text(encoding="utf-8")
    fields = parse_front_matter(raw, path)
    for field in ("titre", "version", "date"):
        if not fields.get(field):
            raise IngestionError(path, f"champ obligatoire introuvable : {field}")

    body = RE_FRONT_MATTER.match(raw).group("body")  # type: ignore[union-attr]
    html = markdown_lib.markdown(body)
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
    return Extracted(
        text=text,
        title=fields["titre"],
        version=fields["version"],
        date=fields["date"],
        ref_produit=None,
    )
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_extract_markdown.py -v`
Attendu : 3 PASS

- [ ] **Step 5 : commit**

```bash
git add ingest/extract.py tests/unit/test_extract_markdown.py
git commit -m "feat(ingest): Markdown extraction (notes internes)"
```

---

## Task 5 : Dérivation des métadonnées

**Files:**
- Create: `ingest/metadata.py`
- Test: `tests/unit/test_metadata.py`

**Interfaces:**
- Consumes: `IngestionError`.
- Produces: alias de types `TypeDoc`, `CollectionName`, `Source` ; `document_id(path) -> str`, `collection_of(path) -> CollectionName`, `type_doc_of(collection: CollectionName) -> TypeDoc`, `family_id(document_id) -> str`, `diversification_group(collection, family, path) -> str`.

Les trois alias sont les `Literal` exacts déclarés par `DocumentCanonique` et `Chunk` (modèles non modifiables) : les définir ici évite un `cast` non vérifié à l'assemblage.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_metadata.py
from pathlib import Path

import pytest

from ingest.errors import IngestionError
from ingest.metadata import (
    collection_of,
    diversification_group,
    document_id,
    family_id,
    type_doc_of,
)


def test_identifiants_de_base():
    p = Path("data/corpus/fiches/REF-1024-v2.1.pdf")
    assert document_id(p) == "REF-1024-v2.1"
    assert collection_of(p) == "fiches"
    assert type_doc_of("fiches") == "fiche_technique"


def test_type_doc_par_collection():
    assert type_doc_of("notices") == "notice"
    assert type_doc_of("sav") == "procedure_sav"
    assert type_doc_of("notes") == "note_interne"


def test_collection_inconnue_echoue():
    with pytest.raises(IngestionError, match="collection inconnue"):
        collection_of(Path("data/corpus/autre/x.pdf"))


def test_family_id_retire_le_suffixe_de_version():
    assert family_id("REF-1024-v2.1") == "REF-1024"
    assert family_id("notice-REF-1459-v1.1") == "notice-REF-1459"
    assert family_id("proc-casse-transport-01-v2.0") == "proc-casse-transport-01"
    # Les notes n'ont pas de version dans leur nom : inchangé.
    assert family_id("note-2024-01-11-alerte-qualite-50") == (
        "note-2024-01-11-alerte-qualite-50"
    )


def test_diversification_group():
    p = Path("x")
    # SAV et notes se regroupent par thème métier (quasi-doublons).
    assert diversification_group("sav", "proc-casse-transport-01", p) == (
        "sav_casse-transport"
    )
    assert diversification_group("notes", "note-2024-01-11-alerte-qualite-50", p) == (
        "note_alerte-qualite"
    )
    # Deux produits distincts ne sont pas des quasi-doublons : groupe = famille.
    assert diversification_group("fiches", "REF-1024", p) == "REF-1024"
    assert diversification_group("notices", "notice-REF-1459", p) == "notice-REF-1459"


def test_theme_non_derivable_echoue():
    with pytest.raises(IngestionError, match="thème"):
        diversification_group("sav", "nom-inattendu", Path("x"))
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_metadata.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'ingest.metadata'`

- [ ] **Step 3 : écrire l'implémentation minimale**

```python
# ingest/metadata.py
"""Dérivation des identifiants et métadonnées à partir du chemin du fichier.

Règles vérifiées sur les 400 fichiers du corpus : 350 familles, 10 thèmes SAV,
5 thèmes de notes, aucune dérivation en échec.
"""

import re
from pathlib import Path
from typing import Literal, cast, get_args

from ingest.errors import IngestionError

# Alias reprenant à l'identique les Literal de DocumentCanonique et Chunk.
CollectionName = Literal["fiches", "notices", "sav", "notes"]
TypeDoc = Literal["fiche_technique", "notice", "procedure_sav", "note_interne"]
Source = Literal["pdf", "html", "md"]

TYPE_DOC_BY_COLLECTION: dict[CollectionName, TypeDoc] = {
    "fiches": "fiche_technique",
    "notices": "notice",
    "sav": "procedure_sav",
    "notes": "note_interne",
}

RE_VERSION_SUFFIX = re.compile(r"-v[\d.]+$")
RE_SAV_THEME = re.compile(r"^proc-(?P<theme>.+?)-\d+$")
RE_NOTE_THEME = re.compile(r"^note-\d{4}-\d{2}-\d{2}-(?P<theme>.+?)-\d+$")


def document_id(path: Path) -> str:
    """Nom du fichier sans extension. Unicité vérifiée sur les 400 fichiers."""
    return path.stem


def collection_of(path: Path) -> CollectionName:
    """Collection = nom du dossier parent, validé contre les valeurs attendues.

    C'est le seul endroit où une chaîne venue du système de fichiers devient un
    Literal : la validation a lieu ici, une fois, plutôt qu'un cast aveugle plus loin.
    """
    name = path.parent.name
    if name not in get_args(CollectionName):
        raise IngestionError(path, f"collection inconnue : {name}")
    return cast(CollectionName, name)


def type_doc_of(collection: CollectionName) -> TypeDoc:
    return TYPE_DOC_BY_COLLECTION[collection]


def family_id(doc_id: str) -> str:
    """Regroupe les versions d'un même document logique."""
    return RE_VERSION_SUFFIX.sub("", doc_id)


def diversification_group(collection: CollectionName, family: str, path: Path) -> str:
    """Regroupe les quasi-doublons métier (pas les versions, voir family_id)."""
    if collection == "sav":
        match = RE_SAV_THEME.match(family)
        if match is None:
            raise IngestionError(path, f"thème SAV non dérivable de : {family}")
        return f"sav_{match.group('theme')}"
    if collection == "notes":
        match = RE_NOTE_THEME.match(family)
        if match is None:
            raise IngestionError(path, f"thème de note non dérivable de : {family}")
        return f"note_{match.group('theme')}"
    # fiches et notices : deux produits distincts ne sont pas des quasi-doublons.
    return family
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_metadata.py -v`
Attendu : 5 PASS

- [ ] **Step 5 : commit**

```bash
git add ingest/metadata.py tests/unit/test_metadata.py
git commit -m "feat(ingest): metadata derivation rules"
```

---

## Task 6 : Assemblage du document canonique

**Files:**
- Create: `ingest/build.py`
- Test: `tests/unit/test_build.py`

**Interfaces:**
- Consumes: `extract_pdf`/`extract_html`/`extract_markdown` (Tasks 2-4), tout `ingest/metadata.py` (Task 5), `DocumentCanonique`.
- Produces: `build_document(path: Path) -> DocumentCanonique`, `EXTRACTORS_BY_SUFFIX: dict[str, Callable[[Path], Extracted]]`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_build.py
from datetime import date
from pathlib import Path

import pytest

from ingest.build import build_document
from ingest.errors import IngestionError

CORPUS = Path("data/corpus")


def test_fiche():
    doc = build_document(CORPUS / "fiches" / "REF-1024-v2.1.pdf")
    assert doc.document_id == "REF-1024-v2.1"
    assert doc.family_id == "REF-1024"
    assert doc.diversification_group == "REF-1024"
    assert doc.collection == "fiches"
    assert doc.type_doc == "fiche_technique"
    assert doc.ref_produit == "REF-1024"
    assert doc.version == "2.1"
    assert doc.date == date(2022, 11, 7)
    assert doc.source == "pdf"


def test_procedure_sav():
    doc = build_document(CORPUS / "sav" / "proc-casse-transport-01-v2.0.html")
    assert doc.family_id == "proc-casse-transport-01"
    assert doc.diversification_group == "sav_casse-transport"
    assert doc.type_doc == "procedure_sav"
    assert doc.ref_produit is None
    assert doc.source == "html"


def test_note():
    doc = build_document(CORPUS / "notes" / "note-2024-01-11-alerte-qualite-50.md")
    assert doc.diversification_group == "note_alerte-qualite"
    assert doc.type_doc == "note_interne"
    assert doc.source == "md"


def test_extension_inconnue_echoue(tmp_path):
    f = tmp_path / "fiches" / "x.txt"
    f.parent.mkdir()
    f.write_text("x", encoding="utf-8")
    with pytest.raises(IngestionError, match="format non pris en charge"):
        build_document(f)
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_build.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'ingest.build'`

- [ ] **Step 3 : écrire l'implémentation minimale**

```python
# ingest/build.py
"""Assemblage : fichier source → DocumentCanonique."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

from ingest.document import DocumentCanonique
from ingest.errors import IngestionError
from ingest.extract import Extracted, extract_html, extract_markdown, extract_pdf
from ingest.metadata import (
    Source,
    collection_of,
    diversification_group,
    document_id,
    family_id,
    type_doc_of,
)

EXTRACTORS_BY_SUFFIX: dict[str, Callable[[Path], Extracted]] = {
    ".pdf": extract_pdf,
    ".html": extract_html,
    ".md": extract_markdown,
}

SOURCE_BY_SUFFIX: dict[str, Source] = {".pdf": "pdf", ".html": "html", ".md": "md"}


def build_document(path: Path) -> DocumentCanonique:
    suffix = path.suffix.lower()
    extractor = EXTRACTORS_BY_SUFFIX.get(suffix)
    if extractor is None:
        raise IngestionError(path, f"format non pris en charge : {suffix}")

    extracted = extractor(path)
    doc_id = document_id(path)
    collection = collection_of(path)
    family = family_id(doc_id)
    return DocumentCanonique(
        document_id=doc_id,
        family_id=family,
        diversification_group=diversification_group(collection, family, path),
        content=extracted.text,
        title=extracted.title,
        type_doc=type_doc_of(collection),
        collection=collection,
        ref_produit=extracted.ref_produit,
        version=extracted.version,
        date=date.fromisoformat(extracted.date),
        source=SOURCE_BY_SUFFIX[suffix],
    )
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_build.py -v`
Attendu : 4 PASS

- [ ] **Step 5 : commit**

```bash
git add ingest/build.py tests/unit/test_build.py
git commit -m "feat(ingest): build DocumentCanonique from source file"
```

---

## Task 7 : Chunking

**Files:**
- Create: `ingest/chunking.py`
- Test: `tests/unit/test_chunking.py`

**Interfaces:**
- Consumes: `DocumentCanonique`, `Chunk`.
- Produces: `to_chunks(doc: DocumentCanonique) -> list[Chunk]`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_chunking.py
from datetime import date

from ingest.chunking import to_chunks
from ingest.document import DocumentCanonique


def _doc() -> DocumentCanonique:
    return DocumentCanonique(
        document_id="REF-1024-v2.1",
        family_id="REF-1024",
        diversification_group="REF-1024",
        content="contenu",
        title="Disjoncteur",
        type_doc="fiche_technique",
        collection="fiches",
        ref_produit="REF-1024",
        version="2.1",
        date=date(2022, 11, 7),
        source="pdf",
    )


def test_un_document_donne_un_chunk():
    chunks = to_chunks(_doc())
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_id == "REF-1024-v2.1#0"
    assert chunk.chunk_index == 0
    assert chunk.document_id == "REF-1024-v2.1"


def test_metadonnees_heritees_du_document():
    chunk = to_chunks(_doc())[0]
    doc = _doc()
    for field in (
        "family_id", "diversification_group", "content", "title",
        "type_doc", "collection", "ref_produit", "version", "date", "source",
    ):
        assert getattr(chunk, field) == getattr(doc, field)
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_chunking.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'ingest.chunking'`

- [ ] **Step 3 : écrire l'implémentation minimale**

```python
# ingest/chunking.py
"""Découpage en chunks.

Sur ce corpus, 1 chunk = 1 document entier (documents très en-deçà d'une taille
justifiant un découpage) : chunk_index vaut toujours 0. Le découpage structurel
décrit en conception reste un repli non implémenté — un découpeur que rien ne
déclenche serait du code mort.
"""

from ingest.chunk import Chunk
from ingest.document import DocumentCanonique


def to_chunks(doc: DocumentCanonique) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=f"{doc.document_id}#0",
            document_id=doc.document_id,
            chunk_index=0,
            family_id=doc.family_id,
            diversification_group=doc.diversification_group,
            content=doc.content,
            title=doc.title,
            type_doc=doc.type_doc,
            collection=doc.collection,
            ref_produit=doc.ref_produit,
            version=doc.version,
            date=doc.date,
            source=doc.source,
        )
    ]
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_chunking.py -v`
Attendu : 2 PASS

- [ ] **Step 5 : commit**

```bash
git add ingest/chunking.py tests/unit/test_chunking.py
git commit -m "feat(ingest): chunking (one document = one chunk)"
```

---

## Task 8 : Embeddings

**Files:**
- Create: `ingest/embedder.py`
- Test: `tests/unit/test_embedder.py`

**Interfaces:**
- Consumes: `Chunk`, `Settings` (Task 1).
- Produces: protocole `Embedder` (méthode `embed(texts: list[str]) -> list[list[float]]`), `AzureEmbedder`, `embedding_text(chunk: Chunk) -> str`, `embed_in_batches(embedder, texts, batch_size=64) -> list[list[float]]`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_embedder.py
from datetime import date

from ingest.chunk import Chunk
from ingest.embedder import embed_in_batches, embedding_text


class FakeEmbedder:
    """Embedder factice : aucun appel réseau, garde la trace des lots reçus."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[float(len(t)), 0.0] for t in texts]


def _chunk(ref: str | None) -> Chunk:
    return Chunk(
        chunk_id="c#0", document_id="c", chunk_index=0, family_id="c",
        diversification_group="c", content="contenu", title="Titre",
        type_doc="fiche_technique", collection="fiches", ref_produit=ref,
        version="1.0", date=date(2024, 1, 1), source="pdf",
    )


def test_texte_embedde_prefixe_titre_et_reference():
    # Décision de conception : le vecteur dense est calculé sur
    # title + ref_produit + content ; le content stocké reste brut.
    assert embedding_text(_chunk("REF-1024")) == "Titre REF-1024 contenu"


def test_reference_absente_omise_du_prefixe():
    assert embedding_text(_chunk(None)) == "Titre contenu"


def test_appels_groupes_par_lots():
    fake = FakeEmbedder()
    vectors = embed_in_batches(fake, [f"t{i}" for i in range(5)], batch_size=2)
    assert [len(b) for b in fake.batches] == [2, 2, 1]
    assert len(vectors) == 5
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_embedder.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'ingest.embedder'`

- [ ] **Step 3 : écrire l'implémentation minimale**

```python
# ingest/embedder.py
"""Calcul des embeddings via Azure AI Foundry (endpoint compatible OpenAI).

Le protocole Embedder existe pour que le pipeline soit testable sans réseau ni
clé d'API : les tests injectent un embedder factice.
"""

from typing import Protocol

from openai import OpenAI

from ingest.chunk import Chunk
from ingest.settings import Settings

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


def embed_in_batches(
    embedder: Embedder, texts: list[str], batch_size: int = BATCH_SIZE
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(embedder.embed(texts[start : start + batch_size]))
    return vectors
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_embedder.py -v`
Attendu : 3 PASS

- [ ] **Step 5 : commit**

```bash
git add ingest/embedder.py tests/unit/test_embedder.py
git commit -m "feat(ingest): Azure embedder and batched embedding"
```

---

## Task 9 : Écriture dans Chroma

**Files:**
- Create: `ingest/store.py`
- Test: `tests/unit/test_store.py`

**Interfaces:**
- Consumes: `Chunk`, `Settings`.
- Produces: `chroma_metadata(chunk: Chunk) -> dict[str, str | int]`, `open_collection(client, name) -> Collection`, `upsert_chunks(collection, chunks, vectors) -> None`, `chroma_client(settings) -> ClientAPI`.

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/unit/test_store.py
from datetime import date

import chromadb

from ingest.chunk import Chunk
from ingest.store import chroma_metadata, open_collection, upsert_chunks


def _chunk(chunk_id: str, ref: str | None) -> Chunk:
    return Chunk(
        chunk_id=chunk_id, document_id=chunk_id.split("#")[0], chunk_index=0,
        family_id="fam", diversification_group="grp", content="contenu",
        title="Titre", type_doc="fiche_technique", collection="fiches",
        ref_produit=ref, version="2.1", date=date(2022, 11, 7), source="pdf",
    )


def test_metadonnees_scalaires_et_date_iso():
    meta = chroma_metadata(_chunk("a#0", "REF-1024"))
    assert meta["date"] == "2022-11-07"  # chaîne, pas objet date
    assert meta["chunk_index"] == 0
    assert meta["ref_produit"] == "REF-1024"
    assert all(isinstance(v, (str, int, float, bool)) for v in meta.values())


def test_reference_absente_omise_pas_none():
    # Chroma refuse une valeur None : la clé doit disparaître.
    meta = chroma_metadata(_chunk("b#0", None))
    assert "ref_produit" not in meta


def test_upsert_accepte_par_chroma_et_idempotent():
    client = chromadb.EphemeralClient()
    collection = open_collection(client, "sorabel_test")
    chunks = [_chunk("a#0", "REF-1024"), _chunk("b#0", None)]
    vectors = [[0.1, 0.2], [0.3, 0.4]]

    upsert_chunks(collection, chunks, vectors)
    assert collection.count() == 2

    # Ré-exécution : mise à jour, pas duplication.
    upsert_chunks(collection, chunks, vectors)
    assert collection.count() == 2
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/unit/test_store.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'ingest.store'`

- [ ] **Step 3 : écrire l'implémentation minimale**

```python
# ingest/store.py
"""Écriture des chunks dans Chroma.

Contraintes vérifiées sur chromadb 0.5 : une valeur de métadonnée doit être
str/int/float/bool ; None et les listes sont refusés (ValueError). Une clé absente
est la seule façon d'exprimer « pas de valeur ».
"""

from urllib.parse import urlparse

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from ingest.chunk import Chunk
from ingest.settings import Settings


def chroma_metadata(chunk: Chunk) -> dict[str, str | int]:
    meta: dict[str, str | int] = {
        "document_id": chunk.document_id,
        "chunk_index": chunk.chunk_index,
        "family_id": chunk.family_id,
        "diversification_group": chunk.diversification_group,
        "title": chunk.title,
        "type_doc": chunk.type_doc,
        "collection": chunk.collection,
        "version": chunk.version,
        "date": chunk.date.isoformat(),
        "source": chunk.source,
    }
    # ref_produit est omise (jamais None) pour sav/ et notes/.
    if chunk.ref_produit:
        meta["ref_produit"] = chunk.ref_produit
    return meta


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


def upsert_chunks(
    collection: Collection, chunks: list[Chunk], vectors: list[list[float]]
) -> None:
    if len(chunks) != len(vectors):
        raise ValueError(f"{len(chunks)} chunks pour {len(vectors)} vecteurs")
    if not chunks:
        return
    collection.upsert(
        ids=[c.chunk_id for c in chunks],
        embeddings=vectors,  # type: ignore[arg-type]
        documents=[c.content for c in chunks],
        metadatas=[chroma_metadata(c) for c in chunks],  # type: ignore[arg-type]
    )
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/unit/test_store.py -v`
Attendu : 3 PASS

- [ ] **Step 5 : commit**

```bash
git add ingest/store.py tests/unit/test_store.py
git commit -m "feat(ingest): Chroma metadata mapping and idempotent upsert"
```

---

## Task 10 : Pipeline, CLI et cible Make

**Files:**
- Create: `ingest/pipeline.py`
- Create: `scripts/ingest.py`
- Modify: `Makefile`
- Create: `tests/integration/__init__.py` (fichier vide)
- Test: `tests/integration/test_ingestion.py`

**Interfaces:**
- Consumes: `build_document` (Task 6), `to_chunks` (Task 7), `embedding_text`/`embed_in_batches` (Task 8), `open_collection`/`upsert_chunks`/`chroma_client` (Task 9), `Settings` (Task 1).
- Produces: `iter_corpus_files(corpus_dir: Path) -> list[Path]`, `ingest_corpus(corpus_dir, collection, embedder) -> int` (retourne le nombre de chunks écrits).

- [ ] **Step 1 : écrire le test qui échoue**

```python
# tests/integration/test_ingestion.py
"""Intégration : ingestion complète du corpus réel, Chroma éphémère, embedder factice.

Ni Docker ni appel réseau : la CI ne lance pas docker compose et n'a pas de clé Azure.
"""

from pathlib import Path

import chromadb
import pytest

from ingest.pipeline import ingest_corpus
from ingest.store import open_collection

CORPUS = Path("data/corpus")

ATTENDU_PAR_COLLECTION = {"fiches": 150, "notices": 80, "sav": 90, "notes": 80}


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0] for t in texts]


@pytest.fixture(scope="module")
def collection():
    client = chromadb.EphemeralClient()
    col = open_collection(client, "sorabel_corpus_test")
    written = ingest_corpus(CORPUS, col, FakeEmbedder())
    assert written == 400
    return col


def test_400_chunks(collection):
    assert collection.count() == 400


def test_repartition_par_collection(collection):
    for name, attendu in ATTENDU_PAR_COLLECTION.items():
        got = collection.get(where={"collection": name}, include=["metadatas"])
        assert len(got["ids"]) == attendu, name


def test_ref_produit_presente_sur_les_pdf_seulement(collection):
    all_meta = collection.get(include=["metadatas"])["metadatas"]
    avec = [m for m in all_meta if "ref_produit" in m]
    assert len(avec) == 230
    assert len(all_meta) - len(avec) == 170


def test_familles_et_groupes(collection):
    from collections import Counter

    all_meta = collection.get(include=["metadatas"])["metadatas"]
    familles = Counter(m["family_id"] for m in all_meta)
    assert len(familles) == 350
    # 50 familles portent deux versions (30 fiches + 10 notices + 10 SAV).
    assert sum(1 for n in familles.values() if n == 2) == 50
    groupes = {m["diversification_group"] for m in all_meta}
    assert len({g for g in groupes if g.startswith("sav_")}) == 10
    assert len({g for g in groupes if g.startswith("note_")}) == 5


def test_reexecution_idempotente(collection):
    ingest_corpus(CORPUS, collection, FakeEmbedder())
    assert collection.count() == 400
```

- [ ] **Step 2 : lancer le test, vérifier qu'il échoue**

Run : `uv run pytest tests/integration/test_ingestion.py -v`
Attendu : FAIL, `ModuleNotFoundError: No module named 'ingest.pipeline'`

- [ ] **Step 3 : écrire l'implémentation minimale**

```python
# ingest/pipeline.py
"""Orchestration de l'ingestion : corpus → chunks → embeddings → Chroma.

Le client Chroma et l'embedder sont injectés : c'est ce qui rend l'ingestion
complète testable sans Docker ni appel réseau.
"""

from collections import Counter
from pathlib import Path

from chromadb.api.models.Collection import Collection

from ingest.build import EXTRACTORS_BY_SUFFIX, build_document
from ingest.chunk import Chunk
from ingest.chunking import to_chunks
from ingest.embedder import Embedder, embed_in_batches, embedding_text
from ingest.errors import IngestionError
from ingest.store import upsert_chunks


def iter_corpus_files(corpus_dir: Path) -> list[Path]:
    """Fichiers du corpus, triés, extensions prises en charge uniquement."""
    files = sorted(
        p for p in corpus_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in EXTRACTORS_BY_SUFFIX
    )
    duplicates = [k for k, n in Counter(p.stem for p in files).items() if n > 1]
    if duplicates:
        raise IngestionError(corpus_dir, f"document_id dupliqués : {duplicates}")
    return files


def ingest_corpus(
    corpus_dir: Path, collection: Collection, embedder: Embedder
) -> int:
    """Ingère tout le corpus et retourne le nombre de chunks écrits."""
    chunks: list[Chunk] = []
    for path in iter_corpus_files(corpus_dir):
        chunks.extend(to_chunks(build_document(path)))

    vectors = embed_in_batches(embedder, [embedding_text(c) for c in chunks])
    upsert_chunks(collection, chunks, vectors)
    return len(chunks)
```

```python
# scripts/ingest.py
"""Ingère le corpus documentaire dans Chroma.

Usage : ``make ingest`` ou ``uv run python scripts/ingest.py``.
Nécessite Chroma (``make up``) et les variables Azure de ``.env``.
"""

from ingest.embedder import AzureEmbedder
from ingest.pipeline import ingest_corpus
from ingest.settings import get_settings
from ingest.store import chroma_client, open_collection


def main() -> None:
    settings = get_settings()
    collection = open_collection(
        chroma_client(settings), settings.chroma_collection
    )
    written = ingest_corpus(
        settings.corpus_dir, collection, AzureEmbedder(settings)
    )
    print(f"{written} chunks ingérés dans « {settings.chroma_collection} »")


if __name__ == "__main__":
    main()
```

Dans le `Makefile`, ajouter `ingest` à la ligne `.PHONY` et la cible, après `seed` :

```makefile
ingest:
	uv run python scripts/ingest.py
```

- [ ] **Step 4 : lancer les tests, vérifier qu'ils passent**

Run : `uv run pytest tests/integration/test_ingestion.py -v`
Attendu : 5 PASS (l'ingestion des 400 fichiers prend quelques secondes)

- [ ] **Step 5 : vérifier lint et typage**

Run : `uv run ruff check . && uv run mypy ingest`
Attendu : aucune erreur

- [ ] **Step 6 : commit**

```bash
git add ingest/pipeline.py scripts/ingest.py Makefile \
        tests/integration/__init__.py tests/integration/test_ingestion.py
git commit -m "feat(ingest): corpus pipeline, CLI entry point and make target"
```

---

## Task 11 : Vérification de bout en bout contre le vrai Chroma

**Files:**
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Consumes: tout le pipeline (Tasks 1-10).
- Produces: rien (vérification manuelle + trace).

Cette tâche n'ajoute pas de code : elle vérifie que le chemin réel — vrai serveur Chroma, vrais embeddings Azure — fonctionne, ce que les tests ne couvrent volontairement pas.

- [ ] **Step 1 : démarrer Chroma**

Run : `make up`
Attendu : conteneur `chroma` démarré, port 8002 ouvert

- [ ] **Step 2 : lancer l'ingestion réelle**

Run : `make ingest`
Attendu : `400 chunks ingérés dans « sorabel_corpus »`

- [ ] **Step 3 : vérifier le contenu de la collection**

```bash
uv run python -c "
from ingest.settings import get_settings
from ingest.store import chroma_client, open_collection
s = get_settings()
col = open_collection(chroma_client(s), s.chroma_collection)
print('chunks :', col.count())
got = col.get(ids=['REF-8842-v2.1#0'], include=['metadatas', 'documents'])
print('meta   :', got['metadatas'][0])
print('dim    :', len(col.get(ids=['REF-8842-v2.1#0'], include=['embeddings'])['embeddings'][0]))
"
```

Attendu : `chunks : 400`, métadonnées avec `ref_produit=REF-8842` et `type_doc=fiche_technique`, dimension du vecteur non nulle (celle du modèle Azure).

- [ ] **Step 4 : vérifier l'idempotence sur le vrai serveur**

Run : `make ingest` une seconde fois, puis recompter
Attendu : toujours `400`

- [ ] **Step 5 : consigner dans le changelog**

Ajouter en tête de `docs/CHANGELOG.md` (après la ligne d'introduction) une entrée sur ce modèle, en remplaçant les valeurs entre chevrons par ce qui a été réellement observé aux étapes 2-4 :

```markdown
## <AAAA-MM-JJ> — Ingestion du corpus opérationnelle

- `ingest/` construit : extraction PDF/HTML/Markdown, dérivation des métadonnées,
  chunking (1 chunk = 1 document), embeddings Azure, écriture Chroma.
- Point d'entrée `scripts/ingest.py` + cible `make ingest`.
- Constaté sur le vrai serveur Chroma : **400 chunks** dans `sorabel_corpus`,
  vecteurs de dimension **<dim>**, ingestion complète en **<durée>**.
- Idempotence vérifiée : seconde exécution de `make ingest`, compte inchangé à 400.
- Couverture : <n> tests unitaires + 5 tests d'intégration (Chroma éphémère,
  embedder factice — ni Docker ni réseau requis en CI).
- Reste ouvert : `tests/acceptance/` et `tests/conftest.py` encodent le contrat de
  `docs/cadrage_dsi.md` (document retiré par le formateur) — à trancher avec lui
  avant le chantier MCP.
```

- [ ] **Step 6 : commit**

```bash
git add docs/CHANGELOG.md
git commit -m "docs: log corpus ingestion results"
```

---

## Ce que ce plan ne fait pas

- Aucun `refs_citees` : E1/E2 n'en ont pas besoin (E2 cherche le document *dont* `ref_produit` correspond, pas ceux qui la mentionnent) et Chroma refuse les listes. À rouvrir seulement si un besoin explicite apparaît.
- Aucun découpeur de documents longs (voir contraintes globales).
- Aucun retrieval, BM25, RRF, reranking ni tool MCP : étapes suivantes.
- Aucune modification de `tests/acceptance/` ni de `tests/conftest.py` (voir le point à valider en tête de plan).
