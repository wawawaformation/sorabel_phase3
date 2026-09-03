# Plan d'implémentation — Text-to-SQL, tools figés et barrières de lecture seule

> **Pour un worker agentique :** SOUS-SKILL REQUIS : utiliser
> `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans`
> pour dérouler ce plan tâche par tâche. Les étapes sont en cases à cocher (`- [ ]`).

**Objectif :** exposer `data/sorabel.db` via quatre opérations — `get_schema`,
`ask_database` (Text-to-SQL génératif), `check_stock` et `order_status` (SQL figé) — en
garantissant la lecture seule par cumul de barrières indépendantes et le filtrage des
colonnes sensibles par profil.

**Architecture :** un module par responsabilité dans `sql/`, orchestrés par
`sql/engine.py` (`SqlEngine`), sur le même patron que `retrieval/engine.py`. Le profil et
les règles d'accès sont **injectés au constructeur**, jamais passés en paramètre de
méthode — c'est ce qui rend le profil non falsifiable. Deux connexions SQLite aux rôles
séparés : l'introspection (avec `PRAGMA`, sans authorizer) et l'exécution (avec authorizer
strict, sans `PRAGMA`).

**Stack :** Python 3.11, `sqlite3` (stdlib, aucune dépendance à ajouter), SDK `openai`
(génération), `pydantic-settings`, `pytest`, `ruff`, `mypy`.

**Spec de référence :** `docs/spec_sql.md`. Conception amont :
`conception/2_text-to-sql/tools_sql_mcp.md` et `questions_reponses_text-to-sql.md`.

## Contraintes globales

- Ligne max **100** caractères (`ruff`), cible `py311`. `mypy` doit passer sur
  `gateway ingest retrieval sql` (`make lint` inclut déjà `sql`).
- Code en **anglais**, commentaires et docstrings en **français**. Docstrings loquaces :
  rôle, pourquoi ce choix, qui consomme quoi (voir `retrieval/` depuis le commit `845274e`).
- **Aucun test de `tests/unit/` ni `tests/integration/` ne doit exiger le réseau.** Le
  client LLM est injecté et remplacé par un double. Seuls `scripts/eval_sql.py` (Task 11)
  et la vérification manuelle de la Task 9 appellent Azure.
- **`data/sorabel.db` n'est jamais modifié.** Provisionnement par `make seed`
  (`scripts/seed.py`, déterministe, graine 8842 — déjà dans le dépôt). Vérifié
  fonctionnellement identique au fichier fourni `brief/data/data/sorabel.db`.
- **Tout test exécutant du DDL utilise une base neuve** (`tmp_path` par test). Leçon vécue
  pendant la rédaction de la spec : un `DROP TABLE` réussi pollue silencieusement tous les
  tests suivants qui partagent le fichier.
- Ne pas toucher : `mcp_server/`, `tests/acceptance/`, `tests/conftest.py` (restent rouges,
  décision actée en spec § 8), `retrieval/`, `ingest/`.
- Commit après **chaque** tâche, après `uv run ruff check .`, `uv run mypy gateway ingest
  retrieval sql` et `uv run pytest tests/unit tests/integration -q` au vert.

### Allowlist de l'authorizer (vérifiée, spec § 2.3)

```text
AUTORISÉS : SQLITE_SELECT (21) | SQLITE_READ (20) | SQLITE_FUNCTION (31)
TOUT LE RESTE -> SQLITE_DENY   (jamais SQLITE_IGNORE, qui tronque sans erreur)

Sur SQLITE_READ, deux refus supplémentaires :
  table commençant par "sqlite_"          -> DENY  (fuite de schéma, spec § 2.4)
  (table, colonne) dans les colonnes      -> DENY  (règle métier du profil)
  interdites du profil

Signature du callback : (action, arg1, arg2, db_name, trigger)
  pour SQLITE_READ  : arg1 = table, arg2 = colonne
  pour SQLITE_SELECT: arg1 = arg2 = None
```

Conséquence vérifiée : `PRAGMA` déclenche `SQLITE_PRAGMA`, hors allowlist, donc refusé.
D'où les deux connexions distinctes.

### Colonnes sensibles (conception § 3.3)

```text
profil support  -> interdites : produits.prix_achat_ht, produits.marge_pct, ventes.marge_ht
profil commercial -> aucune restriction
```

### Types d'exception SQLite (vérifiés, spec § 2.3)

```text
refus de l'authorizer (colonne ou action) -> sqlite3.DatabaseError
                                              "access to X.Y is prohibited" / "not authorized"
colonne ou table inexistante              -> sqlite3.OperationalError (sous-classe de DatabaseError)
requête interrompue par le délai           -> sqlite3.OperationalError "interrupted"
plusieurs instructions                     -> sqlite3.ProgrammingError (refus du driver)
```

### Ordre des tâches et dépendances

```text
Task 1  settings + make seed        (socle, aucune dépendance)
Task 2  sql/access.py               (AccessRules)
Task 3  sql/descriptions.py         (métadonnées métier)
Task 4  sql/schema.py               (get_schema, introspection)   <- 2, 3
Task 5  sql/guard.py connexions     (mode=ro, query_only, authorizer)  <- 2
Task 6  sql/guard.py validation     (structurale, LIMIT, délai)
Task 7  sql/tools.py                (check_stock, order_status)    <- 5
Task 8  sql/trace.py                (TraceRecorder + double journal)
Task 9  sql/generate.py             (contexte + appel LLM)         <- 4
Task 10 sql/engine.py               (SqlEngine, orchestration)     <- tout
Task 11 scripts/eval_sql.py         (mesure sur les 24 questions)  <- 10
```

---

## Task 1 : Réglages et provisionnement de la base

**Files:**

- Modify: `gateway/settings.py` (ajout de 4 champs après `rrf_k`)
- Test: `tests/unit/test_settings.py` (ajout d'un test)

**Interfaces:**

- Consomme : `Settings` (pydantic-settings) existant.
- Produit : `settings.sqlite_path`, `settings.sql_default_limit`, `settings.sql_timeout_s`,
  `settings.sql_alert_log` — consommés par les Tasks 4 à 11.

- [ ] **Étape 1 : provisionner la base**

```bash
make seed
```

Attendu, en sortie : `produits: 120`, `stocks: 312`, `clients: 60`, `commandes: 340`,
`ventes: 993`. Le fichier `data/sorabel.db` est créé. `data/` est déjà dans `.gitignore`
pour le corpus — vérifier que `data/sorabel.db` n'est pas suivi par git
(`git status --short` ne doit pas le lister).

- [ ] **Étape 2 : écrire le test qui échoue**

Dans `tests/unit/test_settings.py`, ajouter :

```python
def test_valeurs_par_defaut_sql():
    # Les réglages SQL sont de la config interne, jamais des paramètres de tool
    # (conception : « config interne, pas un paramètre »).
    settings = Settings(_env_file=None)
    assert settings.sqlite_path == Path("data/sorabel.db")
    assert settings.sql_default_limit == 100
    assert settings.sql_timeout_s == 5.0
    assert settings.sql_alert_log == Path("logs/tentatives_ecriture.jsonl")
```

Si `Path` n'est pas déjà importé dans ce fichier, ajouter `from pathlib import Path`.

- [ ] **Étape 3 : lancer le test, vérifier l'échec**

```bash
uv run pytest tests/unit/test_settings.py::test_valeurs_par_defaut_sql -v
```

Attendu : FAIL avec `AttributeError: 'Settings' object has no attribute 'sqlite_path'`.

- [ ] **Étape 4 : implémenter**

Dans `gateway/settings.py`, après le champ `rrf_k`, ajouter :

```python
    # --- Text-to-SQL : accès à la base et garde-fous (sql/) ---
    sqlite_path: Path = Path("data/sorabel.db")  # généré par make seed, jamais modifié
    sql_default_limit: int = 100  # LIMIT des requêtes de liste ; LIMIT+1 interrogé en interne
    sql_timeout_s: float = 5.0  # délai maximal d'exécution, via set_progress_handler
    # Duplication des seules tentatives d'écriture, pour surveillance directe (spec § 4.11).
    # Le journal MCP unique reste la source de vérité, ce fichier n'en est qu'une vue.
    sql_alert_log: Path = Path("logs/tentatives_ecriture.jsonl")
```

- [ ] **Étape 5 : lancer le test, vérifier le succès**

```bash
uv run pytest tests/unit/test_settings.py -v
```

Attendu : PASS (tous les tests du fichier).

- [ ] **Étape 6 : vérifications complètes et commit**

```bash
uv run ruff check . && uv run mypy gateway ingest retrieval sql
uv run pytest tests/unit tests/integration -q
git add gateway/settings.py tests/unit/test_settings.py
git commit -m "Add Text-to-SQL settings to the shared gateway config"
```

---

## Task 2 : Règles d'accès par profil (`sql/access.py`)

**Files:**

- Create: `sql/access.py`
- Test: `tests/unit/test_access.py`

**Interfaces:**

- Produit :
  - `AccessRules` (Protocol) avec `hidden_columns(profile: str) -> frozenset[tuple[str, str]]`
  - `StaticAccessRules` : implémentation par défaut du chantier
  - `SENSITIVE_COLUMNS: frozenset[tuple[str, str]]`
  - `PROFILES: frozenset[str]`
- Consommé par : Tasks 4 (schema), 5 (authorizer), 10 (engine).

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `tests/unit/test_access.py` :

```python
import pytest

from sql.access import PROFILES, SENSITIVE_COLUMNS, StaticAccessRules


def test_support_ne_voit_pas_les_colonnes_sensibles():
    rules = StaticAccessRules()
    cachees = rules.hidden_columns("support")
    assert ("produits", "prix_achat_ht") in cachees
    assert ("produits", "marge_pct") in cachees
    assert ("ventes", "marge_ht") in cachees
    assert len(cachees) == 3


def test_commercial_ne_subit_aucune_restriction():
    assert StaticAccessRules().hidden_columns("commercial") == frozenset()


def test_profil_inconnu_refuse_plutot_que_de_tout_ouvrir():
    # Un profil non prévu ne doit jamais aboutir à un accès complet par défaut :
    # le comportement sûr est l'erreur, pas le silence permissif.
    with pytest.raises(ValueError, match="profil inconnu"):
        StaticAccessRules().hidden_columns("admin")


def test_profils_et_colonnes_sensibles_exposes_comme_constantes():
    # Les tests d'intégration et l'eval s'appuient sur ces constantes plutôt que
    # de recopier la liste — une seule source de vérité (conception § 3.3).
    assert PROFILES == frozenset({"support", "commercial"})
    assert ("ventes", "marge_ht") in SENSITIVE_COLUMNS
```

- [ ] **Étape 2 : lancer les tests, vérifier l'échec**

```bash
uv run pytest tests/unit/test_access.py -v
```

Attendu : FAIL avec `ModuleNotFoundError: No module named 'sql.access'`.

- [ ] **Étape 3 : implémenter**

Créer `sql/access.py` :

```python
"""Règles d'accès par profil — source de vérité unique du filtrage colonne × profil.

La conception (§ 3.3) impose que cette politique ne soit pas recopiée indépendamment
dans plusieurs fonctions : elle est consommée par ``sql/schema.py`` (filtrage du schéma
présenté), ``sql/guard.py`` (authorizer SQLite) et ``sql/engine.py`` (vérification des
références déclarées par le modèle). D'où le ``Protocol`` : le Chantier 3 pourra injecter
une implémentation adossée à la matrice d'accès MCP formelle sans modifier ``sql/``.
"""

from typing import Protocol

#: Profils métier du projet. Deux suffisent (brief).
PROFILES = frozenset({"support", "commercial"})

#: Colonnes que le profil ``support`` ne doit jamais voir — ni dans le schéma présenté,
#: ni dans le SQL accepté, ni dans le résultat (conception § 3.3, vérifié sur la base).
SENSITIVE_COLUMNS = frozenset({
    ("produits", "prix_achat_ht"),
    ("produits", "marge_pct"),
    ("ventes", "marge_ht"),
})


class AccessRules(Protocol):
    """Contrat minimal : quelles colonnes sont interdites à un profil donné.

    Le profil est passé en argument (et non porté par l'objet) pour qu'une seule
    instance de règles puisse servir plusieurs profils — c'est ``SqlEngine`` qui est
    lié à un profil, pas les règles.
    """

    def hidden_columns(self, profile: str) -> frozenset[tuple[str, str]]: ...


class StaticAccessRules:
    """Implémentation par défaut de ce chantier : la règle connue, en dur mais isolée.

    « En dur » ici veut dire « constante versionnée dans le dépôt », pas « dispersée
    dans le code métier » : un seul endroit à changer, et l'injection permet de la
    remplacer sans toucher aux consommateurs.
    """

    def hidden_columns(self, profile: str) -> frozenset[tuple[str, str]]:
        if profile not in PROFILES:
            raise ValueError(f"profil inconnu : {profile!r}")
        return SENSITIVE_COLUMNS if profile == "support" else frozenset()
```

- [ ] **Étape 4 : lancer les tests, vérifier le succès**

```bash
uv run pytest tests/unit/test_access.py -v
```

Attendu : PASS (4 tests).

- [ ] **Étape 5 : vérifications complètes et commit**

```bash
uv run ruff check . && uv run mypy gateway ingest retrieval sql
uv run pytest tests/unit tests/integration -q
git add sql/access.py tests/unit/test_access.py
git commit -m "Add per-profile column access rules"
```

---

## Task 3 : Descriptions métier des colonnes (`sql/descriptions.py`)

**Files:**

- Create: `sql/descriptions.py`
- Test: `tests/unit/test_descriptions.py`

**Interfaces:**

- Produit :
  - `ColumnDoc` (dataclass frozen) : `description: str`, `values: tuple[str, ...] | None`
  - `COLUMN_DOCS: dict[tuple[str, str], ColumnDoc]`
  - `TABLE_DOCS: dict[str, str]`
- Consommé par : Task 4 (`sql/schema.py`).

**Source du contenu :** `docs/schema.sql`, déjà écrit et vérifié en conception. Le
transposer, ne pas réinventer les libellés.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `tests/unit/test_descriptions.py` :

```python
from sql.descriptions import COLUMN_DOCS, TABLE_DOCS


def test_les_cinq_tables_metier_sont_documentees():
    assert set(TABLE_DOCS) == {"produits", "stocks", "clients", "commandes", "ventes"}


def test_chaque_colonne_du_schema_a_une_description_non_vide():
    # 9 + 5 + 5 + 5 + 7 colonnes selon docs/schema.sql
    assert len(COLUMN_DOCS) == 31
    assert all(doc.description.strip() for doc in COLUMN_DOCS.values())


def test_vocabulaire_ferme_present_la_ou_il_existe():
    # Le modèle génère un meilleur SQL s'il connaît les valeurs possibles
    # (conception § 1.5) : entrepôts, statuts, segments, unités.
    assert COLUMN_DOCS[("stocks", "entrepot")].values == ("LILLE", "LYON", "NANTES")
    assert COLUMN_DOCS[("commandes", "statut")].values == (
        "en_attente", "preparee", "expediee", "livree", "annulee",
    )
    assert COLUMN_DOCS[("produits", "unite")].values == ("pièce", "conditionnement")


def test_colonnes_sans_vocabulaire_ferme_valent_none():
    assert COLUMN_DOCS[("produits", "prix_vente_ht")].values is None


def test_les_colonnes_sensibles_sont_documentees_aussi():
    # Elles sont filtrées par profil au moment de présenter le schéma (sql/schema.py),
    # pas absentes de la documentation : le profil commercial y a droit.
    assert COLUMN_DOCS[("produits", "marge_pct")].description.strip()
    assert COLUMN_DOCS[("ventes", "marge_ht")].description.strip()
```

- [ ] **Étape 2 : lancer les tests, vérifier l'échec**

```bash
uv run pytest tests/unit/test_descriptions.py -v
```

Attendu : FAIL avec `ModuleNotFoundError: No module named 'sql.descriptions'`.

- [ ] **Étape 3 : implémenter**

Créer `sql/descriptions.py` :

```python
"""Descriptions métier des tables et colonnes, superposées à l'introspection SQLite.

Pourquoi ce fichier existe : la structure (tables, colonnes, types, relations) est lue
à la source par ``sql/schema.py`` via ``PRAGMA``, mais SQLite ne stocke aucun commentaire
— vérifié, le ``CREATE TABLE`` réel de ``sorabel.db`` n'en contient pas (spec § 2.2).
Les descriptions, indispensables pour que le modèle génère du SQL juste (conception
§ 1.2), sont donc une couche écrite à la main.

Le contenu est la transposition de ``docs/schema.sql``, rédigé et vérifié en conception.
Aucune génération par LLM : la sensibilité d'une colonne est une décision métier, pas une
inférence linguistique, et régénérer du contenu déjà vérifié n'ajouterait qu'un risque de
dérive (spec § 4.4).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnDoc:
    """Documentation d'une colonne : sa description métier et son vocabulaire fermé.

    ``values`` vaut ``None`` quand la colonne n'a pas d'ensemble de valeurs connu
    (un prix, une date, un libellé libre) — à distinguer d'un tuple vide, qui
    signifierait « aucune valeur possible ».
    """

    description: str
    values: tuple[str, ...] | None = None


TABLE_DOCS: dict[str, str] = {
    "produits": "Catalogue Sorabel : matériel électrique et outillage professionnel.",
    "stocks": "Quantités disponibles par entrepôt, une ligne par référence et entrepôt.",
    "clients": "Comptes professionnels clients.",
    "commandes": "Entêtes de commandes : client, date, statut, montant total.",
    "ventes": "Lignes de commandes, détail produit par produit.",
}

COLUMN_DOCS: dict[tuple[str, str], ColumnDoc] = {
    # --- produits ---
    ("produits", "ref"): ColumnDoc("Référence produit, format REF-NNNN (ex. REF-8842)."),
    ("produits", "nom"): ColumnDoc("Libellé commercial du produit."),
    ("produits", "categorie"): ColumnDoc(
        "Famille de produit : Protection électrique, Câblage, Outillage électroportatif, "
        "EPI, Éclairage, Mesure, Visserie, Distribution, Outillage à main."
    ),
    ("produits", "fabricant"): ColumnDoc("Marque du fournisseur (Voltane, Ferrix, Cablor…)."),
    ("produits", "unite"): ColumnDoc(
        "Unité de vente.", values=("pièce", "conditionnement")
    ),
    ("produits", "prix_vente_ht"): ColumnDoc("Prix public hors taxes, en euros."),
    ("produits", "prix_achat_ht"): ColumnDoc(
        "SENSIBLE — prix d'achat fournisseur hors taxes, en euros."
    ),
    ("produits", "marge_pct"): ColumnDoc(
        "SENSIBLE — marge exprimée en pourcentage du prix de vente."
    ),
    ("produits", "actif"): ColumnDoc(
        "1 = présent au catalogue, 0 = retiré du catalogue.", values=("0", "1")
    ),
    # --- stocks ---
    ("stocks", "id"): ColumnDoc("Identifiant technique de la ligne de stock."),
    ("stocks", "ref"): ColumnDoc("Référence produit concernée (vers produits.ref)."),
    ("stocks", "entrepot"): ColumnDoc(
        "Entrepôt de stockage.", values=("LILLE", "LYON", "NANTES")
    ),
    ("stocks", "quantite"): ColumnDoc(
        "Quantité en stock dans cet entrepôt. Le stock total d'une référence est la "
        "somme sur tous les entrepôts."
    ),
    ("stocks", "seuil_reappro"): ColumnDoc(
        "Seuil déclenchant le réapprovisionnement, propre à cet entrepôt."
    ),
    # --- clients ---
    ("clients", "id"): ColumnDoc("Identifiant interne du client, format CLI-NNNN."),
    ("clients", "raison_sociale"): ColumnDoc("Nom de l'entreprise cliente."),
    ("clients", "segment"): ColumnDoc(
        "Segment commercial du client.",
        values=("artisan", "PME", "grand compte", "collectivité"),
    ),
    ("clients", "ville"): ColumnDoc("Ville du client."),
    ("clients", "email"): ColumnDoc(
        "Contact principal — donnée personnelle, usage interne uniquement."
    ),
    # --- commandes ---
    ("commandes", "id"): ColumnDoc("Identifiant de commande, format CMD-AAAA-NNNN."),
    ("commandes", "client_id"): ColumnDoc("Client ayant passé la commande (vers clients.id)."),
    ("commandes", "date_commande"): ColumnDoc(
        "Date de la commande, au format ISO AAAA-MM-JJ. Seule date du modèle : les "
        "questions temporelles sur les ventes passent par cette colonne."
    ),
    ("commandes", "statut"): ColumnDoc(
        "Statut d'avancement de la commande.",
        values=("en_attente", "preparee", "expediee", "livree", "annulee"),
    ),
    ("commandes", "montant_ht"): ColumnDoc("Total hors taxes de la commande, en euros."),
    # --- ventes ---
    ("ventes", "id"): ColumnDoc("Identifiant technique de la ligne de vente."),
    ("ventes", "commande_id"): ColumnDoc("Commande à laquelle la ligne appartient."),
    ("ventes", "ref"): ColumnDoc("Référence produit vendue (vers produits.ref)."),
    ("ventes", "quantite"): ColumnDoc("Quantité vendue sur cette ligne."),
    ("ventes", "prix_unitaire_ht"): ColumnDoc(
        "Prix unitaire réellement facturé, remise déduite."
    ),
    ("ventes", "remise_pct"): ColumnDoc(
        "Remise accordée sur la ligne, en pourcentage.", values=("0", "5", "10")
    ),
    ("ventes", "marge_ht"): ColumnDoc(
        "SENSIBLE — marge réalisée sur cette ligne de vente, en euros."
    ),
}
```

- [ ] **Étape 4 : lancer les tests, vérifier le succès**

```bash
uv run pytest tests/unit/test_descriptions.py -v
```

Attendu : PASS (5 tests). Si `test_chaque_colonne_du_schema_a_une_description_non_vide`
échoue sur le compte, recompter les colonnes dans `docs/schema.sql` et corriger le
dictionnaire — pas le test : le compte attendu vient du schéma réel.

- [ ] **Étape 5 : vérifications complètes et commit**

```bash
uv run ruff check . && uv run mypy gateway ingest retrieval sql
uv run pytest tests/unit tests/integration -q
git add sql/descriptions.py tests/unit/test_descriptions.py
git commit -m "Add business documentation for database columns"
```

---

## Task 4 : Schéma introspecté et filtré (`sql/schema.py`)

**Files:**

- Create: `sql/schema.py`
- Test: `tests/unit/test_schema.py`

**Interfaces:**

- Consomme : `AccessRules` (Task 2), `COLUMN_DOCS`/`TABLE_DOCS` (Task 3).
- Produit :
  - `ColumnInfo` (frozen) : `name: str`, `type: str`, `description: str`,
    `values: tuple[str, ...] | None`
  - `TableInfo` (frozen) : `name: str`, `description: str`, `columns: tuple[ColumnInfo, ...]`
  - `SchemaResponse` (frozen) : `tables: tuple[TableInfo, ...]`, `relations: tuple[str, ...]`
  - `read_schema(connection, access_rules, profile) -> SchemaResponse`
  - `covered_months(connection) -> dict[str, str]`
  - `schema_as_prompt(schema, months) -> str`
- Consommé par : Tasks 9 (generate), 10 (engine).

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `tests/unit/test_schema.py`. La fixture construit une base jouet — pas la vraie base,
pour que ces tests restent indépendants du contenu métier :

```python
import sqlite3
from pathlib import Path

import pytest

from sql.access import StaticAccessRules
from sql.schema import covered_months, read_schema, schema_as_prompt


@pytest.fixture()
def connection(tmp_path: Path) -> sqlite3.Connection:
    """Base jouet, neuve à chaque test (les tests de DDL ne doivent rien partager)."""
    chemin = tmp_path / "jouet.db"
    con = sqlite3.connect(chemin)
    con.executescript(
        """
        CREATE TABLE produits (
          ref TEXT PRIMARY KEY, nom TEXT NOT NULL, unite TEXT NOT NULL,
          prix_vente_ht REAL NOT NULL, prix_achat_ht REAL NOT NULL, marge_pct REAL NOT NULL
        );
        CREATE TABLE stocks (
          id INTEGER PRIMARY KEY, ref TEXT NOT NULL REFERENCES produits(ref),
          entrepot TEXT NOT NULL, quantite INTEGER NOT NULL, seuil_reappro INTEGER NOT NULL
        );
        CREATE TABLE commandes (
          id TEXT PRIMARY KEY, client_id TEXT NOT NULL, date_commande TEXT NOT NULL,
          statut TEXT NOT NULL, montant_ht REAL NOT NULL
        );
        INSERT INTO commandes VALUES ('CMD-2026-0001', 'CLI-0001', '2026-04-15', 'livree', 10.0);
        INSERT INTO commandes VALUES ('CMD-2025-0002', 'CLI-0002', '2025-10-03', 'livree', 20.0);
        """
    )
    con.commit()
    return con


def test_structure_lue_a_la_source_pas_codee_en_dur(connection):
    schema = read_schema(connection, StaticAccessRules(), "commercial")
    noms = [t.name for t in schema.tables]
    assert noms == ["commandes", "produits", "stocks"]  # ordre alphabétique, stable
    produits = next(t for t in schema.tables if t.name == "produits")
    assert [c.name for c in produits.columns] == [
        "ref", "nom", "unite", "prix_vente_ht", "prix_achat_ht", "marge_pct",
    ]
    assert next(c for c in produits.columns if c.name == "prix_vente_ht").type == "REAL"


def test_tables_internes_sqlite_absentes(connection):
    # sqlite_sequence et compagnie ne font pas partie du schéma métier.
    schema = read_schema(connection, StaticAccessRules(), "commercial")
    assert not any(t.name.startswith("sqlite_") for t in schema.tables)


def test_colonnes_sensibles_absentes_pour_support(connection):
    schema = read_schema(connection, StaticAccessRules(), "support")
    produits = next(t for t in schema.tables if t.name == "produits")
    noms = [c.name for c in produits.columns]
    assert "prix_vente_ht" in noms
    assert "prix_achat_ht" not in noms  # absente, pas masquée
    assert "marge_pct" not in noms


def test_colonnes_sensibles_presentes_pour_commercial(connection):
    schema = read_schema(connection, StaticAccessRules(), "commercial")
    produits = next(t for t in schema.tables if t.name == "produits")
    assert "prix_achat_ht" in [c.name for c in produits.columns]


def test_relations_lues_a_la_source(connection):
    # PRAGMA foreign_key_list expose les relations : pas besoin de les écrire à la main
    # (spec § 2.2).
    schema = read_schema(connection, StaticAccessRules(), "commercial")
    assert "stocks.ref -> produits.ref" in schema.relations


def test_descriptions_metier_jointes_a_la_structure(connection):
    schema = read_schema(connection, StaticAccessRules(), "commercial")
    stocks = next(t for t in schema.tables if t.name == "stocks")
    entrepot = next(c for c in stocks.columns if c.name == "entrepot")
    assert entrepot.description.strip()
    assert entrepot.values == ("LILLE", "LYON", "NANTES")


def test_colonne_sans_description_leve_une_erreur(tmp_path):
    # Une dérive silencieuse entre le schéma réel et sa documentation donnerait un
    # contexte incomplet au modèle : mieux vaut échouer bruyamment (spec § 4.4).
    con = sqlite3.connect(tmp_path / "derive.db")
    con.executescript("CREATE TABLE produits (ref TEXT, colonne_inconnue TEXT);")
    con.commit()
    with pytest.raises(KeyError, match="produits.colonne_inconnue"):
        read_schema(con, StaticAccessRules(), "commercial")


def test_mois_couverts_calcules_depuis_les_donnees(connection):
    # avril -> 2026 et octobre -> 2025 : une devinette « année courante » se tromperait
    # sur octobre (spec § 2.12, § 4.5).
    mois = covered_months(connection)
    assert mois["avril"] == "2026"
    assert mois["octobre"] == "2025"
    assert "mars" not in mois  # aucune commande en mars dans la base jouet


def test_mois_ambigu_exclu_de_la_correspondance(tmp_path):
    con = sqlite3.connect(tmp_path / "ambigu.db")
    con.executescript(
        """
        CREATE TABLE commandes (
          id TEXT PRIMARY KEY, client_id TEXT, date_commande TEXT, statut TEXT, montant_ht REAL
        );
        INSERT INTO commandes VALUES ('A', 'C', '2025-04-01', 'livree', 1.0);
        INSERT INTO commandes VALUES ('B', 'C', '2026-04-01', 'livree', 1.0);
        """
    )
    con.commit()
    # Deux millésimes pour avril : le mois reste légitimement ambigu, donc absent.
    assert "avril" not in covered_months(con)


def test_prompt_ne_contient_pas_les_colonnes_filtrees(connection):
    schema = read_schema(connection, StaticAccessRules(), "support")
    texte = schema_as_prompt(schema, covered_months(connection))
    assert "prix_vente_ht" in texte
    assert "prix_achat_ht" not in texte
    assert "marge_pct" not in texte
    assert "LILLE" in texte  # le vocabulaire fermé aide la génération
    assert "avril -> 2026" in texte
```

- [ ] **Étape 2 : lancer les tests, vérifier l'échec**

```bash
uv run pytest tests/unit/test_schema.py -v
```

Attendu : FAIL avec `ModuleNotFoundError: No module named 'sql.schema'`.

- [ ] **Étape 3 : implémenter**

Créer `sql/schema.py` :

```python
"""Lecture du schéma à la source, filtrée selon le profil — cœur de ``get_schema``.

La structure vient exclusivement de l'introspection SQLite (``PRAGMA table_info`` et
``PRAGMA foreign_key_list``) : ajouter une colonne à la base la fait apparaître ici sans
toucher au code Python, ce qui est l'exigence « lu à la source, jamais codé en dur ». Les
descriptions métier viennent de ``sql/descriptions.py``, parce que SQLite ne stocke aucun
commentaire (vérifié, spec § 2.2).

Attention, contrainte non évidente : ``PRAGMA`` est refusé par l'authorizer strict de
``sql/guard.py`` (vérifié, spec § 2.5). Les fonctions de ce module doivent donc recevoir
la connexion d'**introspection**, celle qui ne porte pas d'authorizer — jamais la
connexion d'exécution.
"""

import sqlite3
from dataclasses import dataclass

from sql.access import AccessRules
from sql.descriptions import COLUMN_DOCS, TABLE_DOCS

#: Numéro de mois -> nom français, pour la correspondance mois/millésime (§ 4.5 de la spec).
MONTH_NAMES = {
    "01": "janvier", "02": "février", "03": "mars", "04": "avril",
    "05": "mai", "06": "juin", "07": "juillet", "08": "août",
    "09": "septembre", "10": "octobre", "11": "novembre", "12": "décembre",
}


@dataclass(frozen=True)
class ColumnInfo:
    """Une colonne telle que présentée au modèle et au client : structure + sens."""

    name: str
    type: str
    description: str
    values: tuple[str, ...] | None


@dataclass(frozen=True)
class TableInfo:
    name: str
    description: str
    columns: tuple[ColumnInfo, ...]


@dataclass(frozen=True)
class SchemaResponse:
    """Réponse du tool ``get_schema`` : tables autorisées et relations entre elles."""

    tables: tuple[TableInfo, ...]
    relations: tuple[str, ...]


def _table_names(connection: sqlite3.Connection) -> list[str]:
    """Tables métier, par ordre alphabétique — les tables internes de SQLite exclues.

    Les ``sqlite_%`` sont écartées ici pour le schéma présenté, et refusées séparément
    à l'exécution par l'authorizer (spec § 2.4) : deux barrières, deux rôles.
    """
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def read_schema(
    connection: sqlite3.Connection, access_rules: AccessRules, profile: str
) -> SchemaResponse:
    """Construit le schéma visible par ce profil, structure introspectée et sens documenté.

    Lève ``KeyError`` si une colonne réelle n'a pas de description : une dérive
    silencieuse entre la base et sa documentation donnerait un contexte incomplet au
    modèle générateur, ce qui est plus dangereux qu'un échec visible (spec § 4.4).
    """
    hidden = access_rules.hidden_columns(profile)
    tables: list[TableInfo] = []
    relations: list[str] = []

    for table in _table_names(connection):
        columns: list[ColumnInfo] = []
        for row in connection.execute(f"PRAGMA table_info({table})"):
            column = str(row[1])
            if (table, column) in hidden:
                continue  # absente de la structure, pas seulement masquée
            doc = COLUMN_DOCS.get((table, column))
            if doc is None:
                raise KeyError(f"colonne sans description : {table}.{column}")
            columns.append(
                ColumnInfo(
                    name=column,
                    type=str(row[2]),
                    description=doc.description,
                    values=doc.values,
                )
            )
        tables.append(
            TableInfo(
                name=table,
                description=TABLE_DOCS.get(table, ""),
                columns=tuple(columns),
            )
        )
        for fk in connection.execute(f"PRAGMA foreign_key_list({table})"):
            relations.append(f"{table}.{fk[3]} -> {fk[2]}.{fk[4]}")

    return SchemaResponse(tables=tuple(tables), relations=tuple(sorted(relations)))


def covered_months(connection: sqlite3.Connection) -> dict[str, str]:
    """Millésime de chaque mois présent dans les commandes, quand il est unique.

    Répond au cas « combien de commandes en avril ? », posé sans année : plutôt que de
    laisser le modèle deviner (comportement instable, mesuré en spec § 2.12), le code
    calcule la correspondance et la lui donne comme un fait. Un mois présent sur deux
    millésimes est volontairement absent du résultat : il reste légitimement ambigu.
    """
    rows = connection.execute(
        "SELECT DISTINCT strftime('%m', date_commande), strftime('%Y', date_commande) "
        "FROM commandes"
    ).fetchall()
    years_by_month: dict[str, set[str]] = {}
    for month, year in rows:
        years_by_month.setdefault(str(month), set()).add(str(year))
    return {
        MONTH_NAMES[month]: next(iter(years))
        for month, years in years_by_month.items()
        if len(years) == 1 and month in MONTH_NAMES
    }


def schema_as_prompt(schema: SchemaResponse, months: dict[str, str]) -> str:
    """Rend le schéma en texte destiné au prompt de génération.

    Une seule fonction produit ce texte, à partir du schéma **déjà filtré** : c'est ce
    qui garantit qu'une colonne interdite ne peut pas réapparaître dans le contexte du
    modèle par une mise en forme parallèle (conception § 3.4).
    """
    blocks: list[str] = []
    for table in schema.tables:
        lignes = [f"TABLE {table.name}" + (f"  -- {table.description}" if table.description else "")]
        for column in table.columns:
            ligne = f"  {column.name} ({column.type}) : {column.description}"
            if column.values:
                ligne += f" Valeurs : {', '.join(column.values)}."
            lignes.append(ligne)
        blocks.append("\n".join(lignes))

    if schema.relations:
        blocks.append("Relations :\n" + "\n".join(f"  {r}" for r in schema.relations))

    if months:
        ordre = list(MONTH_NAMES.values())
        connus = sorted(months, key=ordre.index)
        blocks.append(
            "Millésime de chaque mois présent dans les données :\n"
            + "\n".join(f"  {mois} -> {months[mois]}" for mois in connus)
            + "\nUn mois cité sans année désigne le millésime ci-dessus. Un mois absent "
              "de cette liste est ambigu : demander une clarification."
        )

    return "\n\n".join(blocks)
```

- [ ] **Étape 4 : lancer les tests, vérifier le succès**

```bash
uv run pytest tests/unit/test_schema.py -v
```

Attendu : PASS (10 tests).

- [ ] **Étape 5 : vérifications complètes et commit**

```bash
uv run ruff check . && uv run mypy gateway ingest retrieval sql
uv run pytest tests/unit tests/integration -q
git add sql/schema.py tests/unit/test_schema.py
git commit -m "Read database schema from source, filtered per profile"
```

---

## Task 5 : Connexions et authorizer (`sql/guard.py`, première moitié)

**Files:**

- Create: `sql/guard.py`
- Test: `tests/unit/test_guard_connexions.py`

**Interfaces:**

- Consomme : `AccessRules` (Task 2).
- Produit :
  - `ALLOWED_ACTIONS: frozenset[int]`
  - `open_introspection(path) -> sqlite3.Connection`
  - `open_execution(path, access_rules, profile) -> sqlite3.Connection`
- Consommé par : Tasks 6, 7, 10.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `tests/unit/test_guard_connexions.py` :

```python
import shutil
import sqlite3
from pathlib import Path

import pytest

from sql.access import StaticAccessRules
from sql.guard import open_execution, open_introspection

SCHEMA = """
CREATE TABLE produits (
  ref TEXT PRIMARY KEY, nom TEXT NOT NULL,
  prix_vente_ht REAL NOT NULL, prix_achat_ht REAL NOT NULL, marge_pct REAL NOT NULL
);
CREATE TABLE ventes (
  id INTEGER PRIMARY KEY, ref TEXT NOT NULL, quantite INTEGER NOT NULL,
  marge_ht REAL NOT NULL
);
INSERT INTO produits VALUES ('REF-8842', 'Disjoncteur', 42.0, 21.0, 50.0);
INSERT INTO ventes VALUES (1, 'REF-8842', 3, 12.5);
"""


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Base neuve par test : indispensable, certains tests exécutent du DDL."""
    chemin = tmp_path / "guard.db"
    con = sqlite3.connect(chemin)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return chemin


def test_introspection_autorise_pragma(db_path):
    con = open_introspection(db_path)
    assert con.execute("PRAGMA table_info(produits)").fetchall()


def test_introspection_refuse_l_ecriture(db_path):
    # mode=ro : même sans authorizer, aucune écriture possible.
    con = open_introspection(db_path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("UPDATE produits SET nom='X'")


def test_execution_autorise_les_lectures_legitimes(db_path):
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    assert con.execute("SELECT ref, nom FROM produits").fetchall()
    assert con.execute("SELECT SUM(quantite) FROM ventes").fetchone()[0] == 3
    assert con.execute(
        "SELECT p.nom, SUM(v.quantite) FROM ventes v JOIN produits p ON p.ref = v.ref "
        "GROUP BY p.nom"
    ).fetchall()


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO produits VALUES ('X', 'Y', 1.0, 1.0, 1.0)",
        "UPDATE produits SET nom='X' WHERE ref='REF-8842'",
        "DELETE FROM ventes",
        "DROP TABLE ventes",
        "CREATE TABLE t (a INT)",
        "CREATE VIEW v AS SELECT ref FROM produits",
        "ALTER TABLE produits ADD COLUMN x TEXT",
        "ATTACH DATABASE ':memory:' AS autre",
        "PRAGMA table_info(produits)",
    ],
)
def test_execution_refuse_tout_ce_qui_n_est_pas_lecture(db_path, sql):
    # Allowlist, deny par défaut : une liste noire laisserait passer ce qu'on a oublié
    # d'énumérer — vérifié, un authorizer qui ne filtre que des colonnes laisse
    # passer les UPDATE (spec § 2.3).
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute(sql)


def test_execution_refuse_les_tables_internes_sqlite(db_path):
    # Fuite réelle : sans cette règle, le CREATE TABLE complet est lisible et révèle
    # l'existence des colonnes sensibles (spec § 2.4).
    con = open_execution(db_path, StaticAccessRules(), "support")
    with pytest.raises(sqlite3.DatabaseError, match="sqlite_master"):
        con.execute("SELECT name, sql FROM sqlite_master")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT marge_pct FROM produits",
        "SELECT ref FROM produits ORDER BY marge_pct",
        "SELECT AVG(marge_pct) FROM produits",
        "SELECT * FROM produits",
        "SELECT marge_ht FROM ventes",
    ],
)
def test_colonne_sensible_refusee_pour_support_ou_qu_elle_soit(db_path, sql):
    con = open_execution(db_path, StaticAccessRules(), "support")
    with pytest.raises(sqlite3.DatabaseError, match="prohibited"):
        con.execute(sql)


def test_colonne_sensible_lisible_par_commercial(db_path):
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    assert con.execute("SELECT marge_pct FROM produits").fetchone()[0] == 50.0


def test_plusieurs_instructions_refusees_par_le_driver(db_path):
    # Protection gratuite : le driver Python refuse avant même SQLite (spec § 2.7).
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    with pytest.raises(sqlite3.ProgrammingError):
        con.execute("SELECT 1; SELECT 2")


def test_le_fichier_reste_intact_apres_toutes_ces_tentatives(db_path, tmp_path):
    reference = tmp_path / "reference.db"
    shutil.copy(db_path, reference)
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    for sql in ("DELETE FROM ventes", "DROP TABLE produits"):
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(sql)
    con.close()
    assert db_path.read_bytes() == reference.read_bytes()
```

- [ ] **Étape 2 : lancer les tests, vérifier l'échec**

```bash
uv run pytest tests/unit/test_guard_connexions.py -v
```

Attendu : FAIL avec `ModuleNotFoundError: No module named 'sql.guard'`.

- [ ] **Étape 3 : implémenter**

Créer `sql/guard.py` :

```python
"""Barrières de lecture seule : connexions, authorizer SQLite.

Trois mécanismes cumulés, chacun couvrant un risque que les autres ne couvrent pas
(conception § 2.2, « aucune barrière suffisante isolément ») :

1. ``mode=ro`` — la connexion est ouverte en lecture seule ;
2. ``PRAGMA query_only`` — défense complémentaire au niveau de la connexion ;
3. ``set_authorizer()`` — contrôle ce que SQLite cherche réellement à faire, avec le
   grain de la colonne.

Rappel de la conception § 2.9, qui reste vrai malgré tout ce module : ces trois
mécanismes sont **par connexion**, pas par fichier. Seule la permission fichier au
niveau du système d'exploitation empêche une deuxième connexion non protégée d'écrire.
C'est une condition de déploiement, pas du code.

Deux fonctions d'ouverture, parce que deux usages ont des besoins incompatibles :
l'introspection a besoin de ``PRAGMA`` (que l'authorizer refuse), l'exécution a besoin
de l'authorizer. Cette séparation n'affaiblit rien — la connexion d'introspection
n'exécute que du SQL écrit par nous, jamais du SQL généré par un modèle (spec § 2.5).
"""

import sqlite3
from pathlib import Path

from sql.access import AccessRules

#: Seuls codes d'action nécessaires au SQL de lecture légitime, déterminés en
#: instrumentant dix formes de requêtes réelles : simple, agrégat, COUNT, jointure,
#: GROUP BY + ORDER BY, sous-requête, CTE, fonctions de texte et de date, triple
#: jointure (spec § 2.3). Tout le reste est refusé par défaut.
#: SQLITE_RECURSIVE en est volontairement absent : aucune question du jeu d'évaluation
#: n'a besoin d'une CTE récursive, et borner la complexité générée est un bénéfice.
ALLOWED_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
})


def open_introspection(path: Path) -> sqlite3.Connection:
    """Connexion dédiée à la lecture du schéma : ``PRAGMA`` autorisé, pas d'authorizer.

    Sans authorizer parce que ``PRAGMA`` déclenche ``SQLITE_PRAGMA``, hors allowlist
    (vérifié, spec § 2.5). Le risque est nul : cette connexion n'exécute que des
    ``PRAGMA`` dont notre code écrit le texte, et reste en lecture seule.
    """
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def open_execution(
    path: Path, access_rules: AccessRules, profile: str
) -> sqlite3.Connection:
    """Connexion d'exécution du SQL généré ou figé : lecture seule et authorizer strict.

    L'authorizer fonctionne en **allowlist** : il refuse par défaut et n'autorise que
    les trois codes d'``ALLOWED_ACTIONS``. C'est délibéré — une liste noire laisse
    passer ce qu'on n'a pas pensé à énumérer, et un authorizer qui ne refusait que des
    colonnes sensibles laissait effectivement passer les ``UPDATE`` (spec § 2.3).
    """
    hidden = access_rules.hidden_columns(profile)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")  # avant l'authorizer, qui refuse PRAGMA

    def authorize(action: int, arg1: str | None, arg2: str | None,
                  db_name: str | None, trigger: str | None) -> int:
        if action == sqlite3.SQLITE_READ:
            table = arg1 or ""
            # Les tables internes de SQLite exposent le CREATE TABLE complet, donc
            # l'existence des colonnes sensibles : fuite vérifiée (spec § 2.4).
            if table.startswith("sqlite_"):
                return sqlite3.SQLITE_DENY
            if (table, arg2 or "") in hidden:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        # SQLITE_DENY et jamais SQLITE_IGNORE : IGNORE produirait un résultat
        # silencieusement tronqué, plus trompeur qu'une erreur (conception § 3.7).
        return sqlite3.SQLITE_OK if action in ALLOWED_ACTIONS else sqlite3.SQLITE_DENY

    connection.set_authorizer(authorize)
    return connection
```

- [ ] **Étape 4 : lancer les tests, vérifier le succès**

```bash
uv run pytest tests/unit/test_guard_connexions.py -v
```

Attendu : PASS (22 tests, les `parametrize` comptant chacun pour un).

- [ ] **Étape 5 : vérifications complètes et commit**

```bash
uv run ruff check . && uv run mypy gateway ingest retrieval sql
uv run pytest tests/unit tests/integration -q
git add sql/guard.py tests/unit/test_guard_connexions.py
git commit -m "Add read-only connections with a deny-by-default SQLite authorizer"
```

---

## Task 6 : Validation, `LIMIT` et délai (`sql/guard.py`, seconde moitié)

**Files:**

- Modify: `sql/guard.py` (ajout de fonctions, rien à retirer)
- Test: `tests/unit/test_guard_validation.py`

**Interfaces:**

- Consomme : rien de nouveau.
- Produit :
  - `WRITE_KEYWORDS: frozenset[str]`
  - `ValidationError(Exception)`
  - `validate_sql(sql) -> None` (lève `ValidationError`)
  - `apply_limit(sql, limit) -> str`
  - `run_query(connection, sql, timeout_s, limit) -> tuple[list[str], list[tuple], bool]`
- Consommé par : Tasks 9 (mots-clés), 10 (tout).

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `tests/unit/test_guard_validation.py` :

```python
import sqlite3
from pathlib import Path

import pytest

from sql.access import StaticAccessRules
from sql.guard import ValidationError, apply_limit, open_execution, run_query, validate_sql


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    chemin = tmp_path / "validation.db"
    con = sqlite3.connect(chemin)
    con.executescript(
        "CREATE TABLE ventes (id INTEGER PRIMARY KEY, quantite INTEGER NOT NULL);"
        + "".join(f"INSERT INTO ventes VALUES ({i}, {i});" for i in range(1, 251))
    )
    con.commit()
    con.close()
    return chemin


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT ref FROM produits",
        "select ref from produits",
        "  SELECT ref FROM produits  ",
        "WITH t AS (SELECT ref FROM produits) SELECT ref FROM t",
        "SELECT ref FROM produits;",
    ],
)
def test_formes_de_lecture_acceptees(sql):
    validate_sql(sql)  # ne lève pas


@pytest.mark.parametrize(
    ("sql", "motif"),
    [
        ("INSERT INTO produits VALUES ('X')", "écriture"),
        ("UPDATE produits SET nom='X'", "écriture"),
        ("DELETE FROM ventes", "écriture"),
        ("DROP TABLE ventes", "écriture"),
        ("ALTER TABLE produits ADD COLUMN x TEXT", "écriture"),
        ("CREATE TABLE t (a INT)", "écriture"),
        ("REPLACE INTO produits VALUES ('X')", "écriture"),
        ("ATTACH DATABASE 'x.db' AS x", "écriture"),
        ("SELECT ref FROM produits; DROP TABLE ventes", "une seule instruction"),
        ("SELECT * FROM produits", "SELECT *"),
        ("select  *  from produits", "SELECT *"),
        ("", "vide"),
    ],
)
def test_formes_refusees(sql, motif):
    with pytest.raises(ValidationError, match=motif):
        validate_sql(sql)


def test_select_etoile_refuse_meme_pour_commercial():
    # L'authorizer ne le bloque pas pour un profil sans colonne interdite : la règle
    # garde donc son utilité, pour la lisibilité de la trace (spec § 4.3).
    with pytest.raises(ValidationError, match="SELECT \\*"):
        validate_sql("SELECT * FROM produits")


def test_limit_ajoute_quand_absent():
    assert apply_limit("SELECT id FROM ventes", 100) == "SELECT id FROM ventes LIMIT 101"


def test_limit_existant_respecte():
    sql = "SELECT id FROM ventes LIMIT 5"
    assert apply_limit(sql, 100) == sql


def test_limit_non_ajoute_sur_une_agregation():
    # Un COUNT/SUM retourne naturellement une ligne : un LIMIT n'apporterait rien
    # (conception § 2.7).
    sql = "SELECT COUNT(*) FROM ventes"
    assert apply_limit(sql, 100) == sql


def test_point_virgule_final_gere():
    assert apply_limit("SELECT id FROM ventes;", 100) == "SELECT id FROM ventes LIMIT 101"


def test_resultat_tronque_signale(db_path):
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    colonnes, lignes, tronque = run_query(con, "SELECT id FROM ventes", 5.0, 100)
    assert colonnes == ["id"]
    assert len(lignes) == 100  # la 101e ligne interrogée n'est pas retournée
    assert tronque is True


def test_resultat_complet_non_signale_comme_tronque(db_path):
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    _, lignes, tronque = run_query(con, "SELECT id FROM ventes WHERE id <= 10", 5.0, 100)
    assert len(lignes) == 10
    assert tronque is False


def test_exactement_la_limite_n_est_pas_une_troncature(db_path):
    # Le cas piège que LIMIT+1 résout : 100 lignes reçues ne dit pas s'il y en avait
    # 100 ou 993 (spec § 2.10).
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    _, lignes, tronque = run_query(con, "SELECT id FROM ventes WHERE id <= 100", 5.0, 100)
    assert len(lignes) == 100
    assert tronque is False


def test_requete_trop_longue_interrompue(db_path):
    # set_progress_handler, et non busy_timeout qui ne gère que la contention de
    # verrous (vérifié en conception § 2.7).
    con = open_execution(db_path, StaticAccessRules(), "commercial")
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        run_query(
            con,
            "SELECT COUNT(*) FROM ventes v1, ventes v2, ventes v3, ventes v4",
            0.2,
            100,
        )
```

- [ ] **Étape 2 : lancer les tests, vérifier l'échec**

```bash
uv run pytest tests/unit/test_guard_validation.py -v
```

Attendu : FAIL avec `ImportError: cannot import name 'ValidationError' from 'sql.guard'`.

- [ ] **Étape 3 : implémenter**

Ajouter à la fin de `sql/guard.py` :

```python
#: Mots-clés d'écriture ou de modification de schéma. Sert deux fois : au refus du SQL
#: généré (``validate_sql``) et à la détection d'intention dans la question posée, avant
#: même l'appel au modèle (``sql/generate.py``, spec § 4.11).
#:
#: Faux positif connu et assumé : ``replace`` est aussi une fonction SQLite légitime
#: (``replace(nom, 'a', 'b')``), qui serait donc refusée. Aucune des 24 questions du jeu
#: d'évaluation n'en a besoin, et pencher du côté strict est le bon compromis ici — mais
#: c'est bien une limite, pas une propriété.
WRITE_KEYWORDS = frozenset({
    "insert", "update", "delete", "drop", "alter", "create",
    "replace", "attach", "detach", "truncate", "vacuum", "reindex",
})

#: Repérage grossier d'une agrégation : ces requêtes retournent naturellement peu de
#: lignes, un LIMIT n'y apporterait rien (conception § 2.7).
_AGGREGATE_HINTS = ("count(", "sum(", "avg(", "min(", "max(", "total(", "group_concat(")


class ValidationError(Exception):
    """SQL refusé avant d'atteindre la base, pour une raison structurale."""


def _normalize(sql: str) -> str:
    """Minuscules, espaces resserrés, point-virgule final retiré."""
    return " ".join(sql.lower().split()).rstrip(";").strip()


def validate_sql(sql: str) -> None:
    """Refuse tout ce qui n'est pas une lecture unique et explicite.

    Cette validation double des barrières que SQLite applique déjà (authorizer,
    ``mode=ro``) — c'est volontaire : elle produit un refus motivé et traçable *avant*
    d'atteindre la base, là où l'authorizer ne fournit qu'une exception technique. Elle
    fonctionne en allowlist de formes (`SELECT`, `WITH … SELECT`), la liste de mots-clés
    ne venant qu'en complément (conception § 2.3).
    """
    normalise = _normalize(sql)
    if not normalise:
        raise ValidationError("requête vide")
    if ";" in normalise:
        raise ValidationError("une seule instruction SQL est acceptée")
    # Les mots-clés d'écriture sont testés AVANT la forme : un « INSERT … » doit être
    # refusé pour ce qu'il est (une écriture), pas pour ne pas commencer par SELECT —
    # le motif du refus part dans la trace, il doit être juste.
    mots = set(normalise.replace("(", " ").replace(")", " ").replace(",", " ").split())
    interdits = mots & WRITE_KEYWORDS
    if interdits:
        raise ValidationError(f"mot-clé d'écriture interdit : {', '.join(sorted(interdits))}")
    if not (normalise.startswith("select ") or normalise.startswith("with ")):
        raise ValidationError("seules les formes SELECT et WITH … SELECT sont acceptées")
    if "select *" in normalise or "select  *" in normalise:
        raise ValidationError("SELECT * interdit : colonnes toujours explicites")


def apply_limit(sql: str, limit: int) -> str:
    """Ajoute ``LIMIT limit + 1`` si la requête n'a pas de limite et n'est pas agrégée.

    Le ``+ 1`` est la ruse qui rend la troncature détectable : recevoir exactement
    ``limit`` lignes ne dit pas s'il y en avait davantage (spec § 2.10). ``run_query``
    ne retourne ensuite que ``limit`` lignes au plus.
    """
    normalise = _normalize(sql)
    if " limit " in f" {normalise} " or any(h in normalise for h in _AGGREGATE_HINTS):
        return sql
    return f"{sql.rstrip().rstrip(';')} LIMIT {limit + 1}"


def run_query(
    connection: sqlite3.Connection, sql: str, timeout_s: float, limit: int
) -> tuple[list[str], list[tuple], bool]:
    """Exécute la requête sous délai maximal et retourne (colonnes, lignes, tronqué).

    Le délai s'appuie sur ``set_progress_handler`` : un callback appelé tous les 1000
    opcodes de la machine virtuelle SQLite, qui interrompt en retournant une valeur non
    nulle. ``PRAGMA busy_timeout`` ne conviendrait pas — il ne concerne que l'attente
    d'un verrou, pas la durée d'un calcul (vérifié en conception § 2.7).

    Le handler est retiré en sortie, y compris en cas d'erreur : une connexion
    réutilisée ne doit pas hériter du délai d'une requête précédente.
    """
    deadline = time.monotonic() + timeout_s

    def interrupt_if_late() -> int:
        return 1 if time.monotonic() > deadline else 0

    connection.set_progress_handler(interrupt_if_late, 1000)
    try:
        cursor = connection.execute(sql)
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description] if cursor.description else []
    finally:
        connection.set_progress_handler(None, 0)

    truncated = len(rows) > limit
    return columns, rows[:limit], truncated
```

Ajouter `import time` en tête du fichier, après `import sqlite3`.

- [ ] **Étape 4 : lancer les tests, vérifier le succès**

```bash
uv run pytest tests/unit/test_guard_validation.py -v
```

Attendu : PASS (24 tests).

- [ ] **Étape 5 : vérifications complètes et commit**

```bash
uv run ruff check . && uv run mypy gateway ingest retrieval sql
uv run pytest tests/unit tests/integration -q
git add sql/guard.py tests/unit/test_guard_validation.py
git commit -m "Add SQL structural validation, default LIMIT and execution timeout"
```

---

## Task 7 : Tools SQL figés (`sql/tools.py`)

**Files:**

- Create: `sql/tools.py`
- Test: `tests/unit/test_tools.py`

**Interfaces:**

- Consomme : `open_execution`, `run_query` (Tasks 5, 6).
- Produit :
  - `WarehouseStock` (frozen) : `entrepot: str`, `quantite: int`
  - `CheckStockResult` (frozen) : `ref: str`, `found: bool`, `total_quantity: int`,
    `by_warehouse: tuple[WarehouseStock, ...]`
  - `OrderStatusResult` (frozen) : `order_id: str`, `found: bool`, `status: str | None`,
    `date_commande: str | None`, `montant_ht: float | None`
  - `check_stock(connection, ref) -> CheckStockResult`
  - `order_status(connection, order_id) -> OrderStatusResult`
- Consommé par : Task 10.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `tests/unit/test_tools.py` :

```python
import sqlite3
from pathlib import Path

import pytest

from sql.access import StaticAccessRules
from sql.guard import open_execution
from sql.tools import check_stock, order_status


@pytest.fixture()
def connection(tmp_path: Path) -> sqlite3.Connection:
    chemin = tmp_path / "tools.db"
    con = sqlite3.connect(chemin)
    con.executescript(
        """
        CREATE TABLE stocks (
          id INTEGER PRIMARY KEY, ref TEXT NOT NULL, entrepot TEXT NOT NULL,
          quantite INTEGER NOT NULL, seuil_reappro INTEGER NOT NULL
        );
        CREATE TABLE commandes (
          id TEXT PRIMARY KEY, client_id TEXT NOT NULL, date_commande TEXT NOT NULL,
          statut TEXT NOT NULL, montant_ht REAL NOT NULL
        );
        INSERT INTO stocks VALUES (1, 'REF-8842', 'LILLE', 247, 40);
        INSERT INTO stocks VALUES (2, 'REF-8842', 'LYON', 100, 40);
        INSERT INTO stocks VALUES (3, 'REF-8842', 'NANTES', 427, 40);
        INSERT INTO commandes VALUES ('CMD-2026-0001', 'CLI-0007', '2026-04-15', 'livree', 512.4);
        """
    )
    con.commit()
    con.close()
    return open_execution(chemin, StaticAccessRules(), "support")


def test_stock_agrege_sur_tous_les_entrepots(connection):
    resultat = check_stock(connection, "REF-8842")
    assert resultat.found is True
    assert resultat.total_quantity == 774
    assert [(w.entrepot, w.quantite) for w in resultat.by_warehouse] == [
        ("LILLE", 247), ("LYON", 100), ("NANTES", 427),
    ]


def test_stock_reference_inconnue_pas_une_erreur(connection):
    resultat = check_stock(connection, "REF-0000")
    assert resultat.found is False
    assert resultat.total_quantity == 0
    assert resultat.by_warehouse == ()


def test_stock_injection_impossible(connection):
    # Requête paramétrée : la référence est une valeur, jamais du SQL.
    resultat = check_stock(connection, "REF-8842' OR '1'='1")
    assert resultat.found is False


def test_statut_commande_trouvee(connection):
    resultat = order_status(connection, "CMD-2026-0001")
    assert resultat.found is True
    assert resultat.status == "livree"
    assert resultat.date_commande == "2026-04-15"
    assert resultat.montant_ht == 512.4


def test_commande_introuvable_retourne_found_false(connection):
    # CMD-2026-0042 n'existe pas dans la vraie base non plus : ce n'est pas une
    # erreur SQL mais une réponse légitime (conception § 4.2).
    resultat = order_status(connection, "CMD-2026-0042")
    assert resultat.found is False
    assert resultat.status is None
    assert resultat.date_commande is None
    assert resultat.montant_ht is None
```

- [ ] **Étape 2 : lancer les tests, vérifier l'échec**

```bash
uv run pytest tests/unit/test_tools.py -v
```

Attendu : FAIL avec `ModuleNotFoundError: No module named 'sql.tools'`.

- [ ] **Étape 3 : implémenter**

Créer `sql/tools.py` :

```python
"""Tools SQL figés : requêtes paramétrées, déterministes, sans LLM.

Pourquoi des tools figés à côté de ``ask_database`` : ces deux besoins sont stables et
connus d'avance, seule la valeur du paramètre change. Y répondre par du SQL écrit à la
main donne un déterminisme total, aucun coût de tokens, une latence minimale et une
surface de sécurité triviale — un LLM n'apporterait rien ici (conception § 4.4).

Les requêtes sont **paramétrées** (``?``), jamais construites par concaténation : la
référence ou l'identifiant reçus sont des valeurs, jamais du SQL.
"""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class WarehouseStock:
    entrepot: str
    quantite: int


@dataclass(frozen=True)
class CheckStockResult:
    """Stock d'une référence : total et détail par entrepôt.

    Le détail est inclus parce qu'une référence a plusieurs lignes dans ``stocks``, une
    par entrepôt (vérifié : REF-8842 est présente à LILLE, LYON et NANTES) — le total
    seul masquerait une information utile au métier.
    """

    ref: str
    found: bool
    total_quantity: int
    by_warehouse: tuple[WarehouseStock, ...]


@dataclass(frozen=True)
class OrderStatusResult:
    """Statut d'une commande. ``found=False`` n'est pas une erreur, juste une absence."""

    order_id: str
    found: bool
    status: str | None
    date_commande: str | None
    montant_ht: float | None


def check_stock(connection: sqlite3.Connection, ref: str) -> CheckStockResult:
    """Stock total et par entrepôt d'une référence produit.

    Une référence absente retourne ``found=False`` avec un total à zéro plutôt qu'une
    exception : l'appelant distingue ainsi « rien en stock » de « référence inconnue »
    par le drapeau, sans avoir à gérer une erreur.
    """
    rows = connection.execute(
        "SELECT entrepot, quantite FROM stocks WHERE ref = ? ORDER BY entrepot", (ref,)
    ).fetchall()
    by_warehouse = tuple(WarehouseStock(entrepot=str(r[0]), quantite=int(r[1])) for r in rows)
    return CheckStockResult(
        ref=ref,
        found=bool(by_warehouse),
        total_quantity=sum(w.quantite for w in by_warehouse),
        by_warehouse=by_warehouse,
    )


def order_status(connection: sqlite3.Connection, order_id: str) -> OrderStatusResult:
    """Statut, date et montant d'une commande.

    Commande introuvable -> ``found=False`` et champs à ``None``, pas d'exception
    (conception § 4.2 : ``CMD-2026-0042`` du jeu d'évaluation n'existe pas, et c'est
    une réponse légitime, pas une panne).
    """
    row = connection.execute(
        "SELECT statut, date_commande, montant_ht FROM commandes WHERE id = ?", (order_id,)
    ).fetchone()
    if row is None:
        return OrderStatusResult(
            order_id=order_id, found=False, status=None, date_commande=None, montant_ht=None
        )
    return OrderStatusResult(
        order_id=order_id,
        found=True,
        status=str(row[0]),
        date_commande=str(row[1]),
        montant_ht=float(row[2]),
    )
```

- [ ] **Étape 4 : lancer les tests, vérifier le succès**

```bash
uv run pytest tests/unit/test_tools.py -v
```

Attendu : PASS (6 tests).

- [ ] **Étape 5 : vérifications complètes et commit**

```bash
uv run ruff check . && uv run mypy gateway ingest retrieval sql
uv run pytest tests/unit tests/integration -q
git add sql/tools.py tests/unit/test_tools.py
git commit -m "Add fixed SQL tools for stock and order status"
```

---

## Task 8 : Trace et journal d'alerte (`sql/trace.py`)

**Files:**

- Create: `sql/trace.py`
- Test: `tests/unit/test_trace.py`

**Interfaces:**

- Produit :
  - `TraceRecorder` (Protocol) : `record(entry: dict[str, object]) -> None`
  - `JsonlTraceRecorder` : `__init__(journal_path, alert_path)`, `record(entry)`
  - `NullTraceRecorder` : pour les tests qui ne vérifient pas la trace
- Consommé par : Task 10.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `tests/unit/test_trace.py` :

```python
import json
from pathlib import Path

from sql.trace import JsonlTraceRecorder, NullTraceRecorder


def _lignes(chemin: Path) -> list[dict]:
    if not chemin.exists():
        return []
    return [json.loads(x) for x in chemin.read_text("utf-8").splitlines() if x.strip()]


def test_toute_entree_va_dans_le_journal_unique(tmp_path):
    journal, alerte = tmp_path / "audit.jsonl", tmp_path / "alerte.jsonl"
    recorder = JsonlTraceRecorder(journal, alerte)
    recorder.record({"tool": "ask_database", "statut": "ok", "code": None})
    recorder.record({"tool": "ask_database", "statut": "refused", "code": "OUT_OF_SCHEMA"})
    assert len(_lignes(journal)) == 2


def test_seules_les_tentatives_d_ecriture_sont_dupliquees(tmp_path):
    # Le second fichier est une vue filtrée pour surveillance directe, jamais un
    # journal parallèle : le journal unique reste la source de vérité (spec § 4.11).
    journal, alerte = tmp_path / "audit.jsonl", tmp_path / "alerte.jsonl"
    recorder = JsonlTraceRecorder(journal, alerte)
    recorder.record({"tool": "ask_database", "statut": "ok", "code": None})
    recorder.record({"tool": "ask_database", "statut": "refused", "code": "OUT_OF_SCHEMA"})
    recorder.record({"tool": "ask_database", "statut": "refused", "code": "FORBIDDEN"})
    assert len(_lignes(journal)) == 3
    alertes = _lignes(alerte)
    assert len(alertes) == 1
    assert alertes[0]["code"] == "FORBIDDEN"


def test_les_dossiers_sont_crees_au_besoin(tmp_path):
    recorder = JsonlTraceRecorder(tmp_path / "a" / "audit.jsonl", tmp_path / "b" / "alerte.jsonl")
    recorder.record({"tool": "ask_database", "statut": "refused", "code": "FORBIDDEN"})
    assert (tmp_path / "a" / "audit.jsonl").exists()
    assert (tmp_path / "b" / "alerte.jsonl").exists()


def test_ecriture_append_jamais_ecrasement(tmp_path):
    journal, alerte = tmp_path / "audit.jsonl", tmp_path / "alerte.jsonl"
    JsonlTraceRecorder(journal, alerte).record({"statut": "ok", "code": None})
    JsonlTraceRecorder(journal, alerte).record({"statut": "ok", "code": None})
    assert len(_lignes(journal)) == 2  # une trace est immuable, on n'écrase jamais


def test_accents_lisibles_dans_le_journal(tmp_path):
    journal, alerte = tmp_path / "audit.jsonl", tmp_path / "alerte.jsonl"
    JsonlTraceRecorder(journal, alerte).record({"motif": "pertinence insuffisante", "code": None})
    assert "pertinence insuffisante" in journal.read_text("utf-8")


def test_null_recorder_n_ecrit_rien_et_ne_plante_pas():
    NullTraceRecorder().record({"statut": "ok", "code": "FORBIDDEN"})
```

- [ ] **Étape 2 : lancer les tests, vérifier l'échec**

```bash
uv run pytest tests/unit/test_trace.py -v
```

Attendu : FAIL avec `ModuleNotFoundError: No module named 'sql.trace'`.

- [ ] **Étape 3 : implémenter**

Créer `sql/trace.py` :

```python
"""Journalisation des appels (E5) — interface injectée, implémentation locale.

La conception impose un **journal unique** pour tout le serveur MCP, partagé avec le
RAG : « pas un journal par chantier ». Ce journal appartient donc au chantier MCP, qui
n'existe pas encore. D'où le ``Protocol`` : ``SqlEngine`` écrit à travers cette
interface, et le Chantier 3 injectera le journal réel sans modifier ``sql/``.

``JsonlTraceRecorder`` duplique en plus les seules entrées ``code="FORBIDDEN"`` (les
tentatives d'écriture) dans un seul fichier dédié, pensé pour une surveillance directe
par ``tail -f`` sans avoir à filtrer le journal général. Cette duplication n'est jamais
une source primaire : en cas de divergence, le journal unique fait foi (spec § 4.11).
"""

import json
from pathlib import Path
from typing import Protocol

#: Code de journal des tentatives d'écriture, seul dupliqué dans le fichier d'alerte.
FORBIDDEN = "FORBIDDEN"


class TraceRecorder(Protocol):
    """Contrat minimal : enregistrer une entrée de journal déjà constituée.

    Volontairement un dict et non un type structuré : l'enveloppe exacte du journal
    est définie par la conception MCP (`journal_mcp.md`) et sera figée au Chantier 3.
    Contraindre sa forme ici obligerait à la redéfinir deux fois.
    """

    def record(self, entry: dict[str, object]) -> None: ...


class JsonlTraceRecorder:
    """Écriture JSONL append-only : le journal unique, plus la vue filtrée des alertes.

    Le format JSONL est retenu plutôt qu'une table SQL parce qu'une trace est
    naturellement append-only et se lit directement (``cat``, ``tail -f``) — et parce
    qu'écrire dans ``sorabel.db`` contredirait la lecture seule du fichier métier
    (conception § 2.8).
    """

    def __init__(self, journal_path: Path, alert_path: Path) -> None:
        self._journal_path = journal_path
        self._alert_path = alert_path

    def record(self, entry: dict[str, object]) -> None:
        self._append(self._journal_path, entry)
        if entry.get("code") == FORBIDDEN:
            self._append(self._alert_path, entry)

    @staticmethod
    def _append(path: Path, entry: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # ensure_ascii=False : le journal doit rester lisible à l'œil, accents compris.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


class NullTraceRecorder:
    """N'enregistre rien. Pour les tests qui n'ont rien à vérifier sur la trace."""

    def record(self, entry: dict[str, object]) -> None:
        return None
```

- [ ] **Étape 4 : lancer les tests, vérifier le succès**

```bash
uv run pytest tests/unit/test_trace.py -v
```

Attendu : PASS (6 tests).

- [ ] **Étape 5 : vérifications complètes et commit**

```bash
uv run ruff check . && uv run mypy gateway ingest retrieval sql
uv run pytest tests/unit tests/integration -q
git add sql/trace.py tests/unit/test_trace.py
git commit -m "Add JSONL trace recorder with a dedicated write-attempt alert log"
```

---

## Task 9 : Génération du SQL (`sql/generate.py`)

**Files:**

- Create: `sql/generate.py`
- Test: `tests/unit/test_generate.py`

**Interfaces:**

- Consomme : `schema_as_prompt` (Task 4), `WRITE_KEYWORDS` (Task 6).
- Produit :
  - `GenerationStatus` : `Literal["SQL_GENERABLE", "AMBIGUOUS", "OUT_OF_SCHEMA"]`
  - `Generation` (frozen) : `status`, `sql: str`, `tables: tuple[str, ...]`,
    `columns: tuple[str, ...]`, `clarification: str`, `reason: str`
  - `RESPONSE_SCHEMA: dict`
  - `SYSTEM_PROMPT_TEMPLATE: str`
  - `looks_like_write(question) -> bool`
  - `generate_sql(client, model, question, schema_prompt) -> Generation`
- Consommé par : Task 10.

- [ ] **Étape 1 : écrire les tests qui échouent**

Créer `tests/unit/test_generate.py`. Le client LLM est un double : aucun appel réseau.

```python
import json

import pytest

from sql.generate import Generation, generate_sql, looks_like_write


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = FakeMessage(content)


class FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    """Enregistre les arguments reçus, pour vérifier la forme de l'appel."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.last_kwargs: dict = {}

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeCompletion(json.dumps(self._payload))


class FakeChat:
    def __init__(self, completions: FakeCompletions) -> None:
        self.completions = completions


class FakeClient:
    def __init__(self, payload: dict) -> None:
        self.completions = FakeCompletions(payload)
        self.chat = FakeChat(self.completions)


def _payload(**overrides) -> dict:
    base = {
        "status": "SQL_GENERABLE",
        "sql": "SELECT SUM(quantite) FROM stocks WHERE ref = 'REF-8842'",
        "tables_referencees": ["stocks"],
        "colonnes_referencees": ["stocks.ref", "stocks.quantite"],
        "clarification": "",
        "reason": "",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "question",
    [
        "supprime les commandes de test",
        "mets à jour le prix de la REF-8842 à 89,90",
        "insère un client de démonstration",
        "vide la table ventes",
        "DROP TABLE ventes",
    ],
)
def test_intention_d_ecriture_detectee_avant_le_llm(question):
    # Détection en amont pour que la trace distingue une tentative d'écriture d'une
    # simple question hors périmètre (spec § 4.11).
    assert looks_like_write(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "combien de commandes en avril ?",
        "quel est le stock total de la REF-8842 ?",
        "quelle est la météo à Lille demain ?",
        "les 5 produits les plus vendus en quantité",
    ],
)
def test_question_de_lecture_non_detectee_comme_ecriture(question):
    assert looks_like_write(question) is False


def test_generation_decodee_dans_un_objet_type():
    client = FakeClient(_payload())
    resultat = generate_sql(client, "gpt-5.4-mini", "stock de REF-8842 ?", "TABLE stocks…")
    assert isinstance(resultat, Generation)
    assert resultat.status == "SQL_GENERABLE"
    assert resultat.tables == ("stocks",)
    assert resultat.columns == ("stocks.ref", "stocks.quantite")


def test_appel_structure_strict_et_max_completion_tokens():
    # gpt-5.4-mini refuse max_tokens (vérifié) ; le json_schema strict garantit que
    # la réponse est décodable sans parsing défensif (spec § 2.11).
    client = FakeClient(_payload())
    generate_sql(client, "gpt-5.4-mini", "stock ?", "TABLE stocks…")
    kwargs = client.completions.last_kwargs
    assert "max_completion_tokens" in kwargs
    assert "max_tokens" not in kwargs
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["strict"] is True


def test_schema_filtre_transmis_dans_le_prompt_systeme():
    client = FakeClient(_payload())
    generate_sql(client, "gpt-5.4-mini", "stock ?", "TABLE stocks\n  quantite (INTEGER)")
    systeme = client.completions.last_kwargs["messages"][0]["content"]
    assert "TABLE stocks" in systeme
    assert "quantite (INTEGER)" in systeme


def test_statut_ambigu_sans_sql():
    client = FakeClient(_payload(
        status="AMBIGUOUS", sql="", tables_referencees=[], colonnes_referencees=[],
        clarification="Quel critère définit le meilleur client ?",
    ))
    resultat = generate_sql(client, "gpt-5.4-mini", "le meilleur client ?", "…")
    assert resultat.status == "AMBIGUOUS"
    assert resultat.sql == ""
    assert resultat.clarification.startswith("Quel critère")


def test_statut_hors_schema_sans_sql():
    client = FakeClient(_payload(
        status="OUT_OF_SCHEMA", sql="", tables_referencees=[], colonnes_referencees=[],
        reason="La question porte sur la météo.",
    ))
    resultat = generate_sql(client, "gpt-5.4-mini", "météo ?", "…")
    assert resultat.status == "OUT_OF_SCHEMA"
    assert resultat.sql == ""


def test_reponse_vide_du_modele_traitee_comme_hors_schema():
    # Robustesse : plutôt qu'une exception qui remonterait brute jusqu'au client.
    client = FakeClient({})
    resultat = generate_sql(client, "gpt-5.4-mini", "stock ?", "…")
    assert resultat.status == "OUT_OF_SCHEMA"
```

- [ ] **Étape 2 : lancer les tests, vérifier l'échec**

```bash
uv run pytest tests/unit/test_generate.py -v
```

Attendu : FAIL avec `ModuleNotFoundError: No module named 'sql.generate'`.

- [ ] **Étape 3 : implémenter**

Créer `sql/generate.py` :

```python
"""Génération du SQL : classification et traduction en un seul appel structuré.

Le modèle reçoit un contexte contrôlé — le schéma **déjà filtré** par le profil, les
relations, les particularités métier, la correspondance mois/millésime — et retourne un
objet JSON conforme à un schéma strict. Un seul appel fait à la fois la classification
(la question est-elle traduisible, ambiguë, ou hors périmètre ?) et la génération : le
modèle ne produit du SQL que si le statut est ``SQL_GENERABLE`` (conception § 5.5).

Le modèle déclare aussi les tables et colonnes qu'il compte utiliser. Cette déclaration
est vérifiée par ``sql/engine.py`` contre le schéma filtré, avant même de regarder le
SQL : détecter une intention hors périmètre est plus simple sur une liste que sur une
syntaxe SQL avec alias, jointures et sous-requêtes (conception § 2.11). Elle ne remplace
rien en aval — le modèle peut déclarer honnêtement et générer autre chose.
"""

import json
from dataclasses import dataclass
from typing import Any, Literal

from sql.guard import WRITE_KEYWORDS

GenerationStatus = Literal["SQL_GENERABLE", "AMBIGUOUS", "OUT_OF_SCHEMA"]

MAX_SQL_TOKENS = 900

#: Sortie structurée stricte : vérifié comme supporté par gpt-5.4-mini (spec § 2.11).
#: Tous les champs sont requis — c'est une contrainte du mode strict — d'où les chaînes
#: vides pour les champs non pertinents à un statut donné.
RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "sql_generation",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["SQL_GENERABLE", "AMBIGUOUS", "OUT_OF_SCHEMA"],
            },
            "sql": {"type": "string"},
            "tables_referencees": {"type": "array", "items": {"type": "string"}},
            "colonnes_referencees": {"type": "array", "items": {"type": "string"}},
            "clarification": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": [
            "status", "sql", "tables_referencees", "colonnes_referencees",
            "clarification", "reason",
        ],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT_TEMPLATE = """\
Tu traduis une question métier en SQL SQLite, pour la base de Sorabel, distributeur de
matériel électrique.

{schema}

Règles impératives :
- Utilise UNIQUEMENT les tables et colonnes listées ci-dessus. Toute autre colonne
  n'existe pas ou n'est pas accessible à cet appelant.
- SELECT uniquement (ou WITH ... SELECT). Jamais INSERT, UPDATE, DELETE, DROP, ALTER,
  CREATE, REPLACE, ATTACH.
- Jamais SELECT * : colonnes toujours explicites.
- La table ventes n'a AUCUNE colonne de date : pour une question temporelle sur les
  ventes, joindre commandes et utiliser commandes.date_commande.
- Une référence produit a plusieurs lignes dans stocks, une par entrepôt : un stock
  total est une somme.
- status = SQL_GENERABLE : la question est traduisible sans deviner d'interprétation
  métier. Déclare alors dans tables_referencees et colonnes_referencees (format
  "table.colonne") tout ce que ton SQL utilise réellement, jointures comprises.
- status = AMBIGUOUS : la question est dans le périmètre mais un critère métier est
  indéfini (« le meilleur client », « ça se vend bien »). Propose une clarification,
  ne devine pas.
- status = OUT_OF_SCHEMA : la question ne concerne pas ces données, OU la donnée
  nécessaire n'existe pas parmi les colonnes listées (par exemple une marge ou un coût
  d'achat absent). Dans ce cas c'est OUT_OF_SCHEMA et non AMBIGUOUS : ne demande pas de
  clarification pour une donnée que tu n'as pas, et n'explique pas ce qui manque.
- Si status n'est pas SQL_GENERABLE : sql = "" et les deux listes sont vides.
- Champs non pertinents : chaîne vide.\
"""


@dataclass(frozen=True)
class Generation:
    """Sortie du modèle, décodée. ``sql`` est vide sauf si ``status`` vaut SQL_GENERABLE."""

    status: GenerationStatus
    sql: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    clarification: str
    reason: str


def looks_like_write(question: str) -> bool:
    """Détecte une intention d'écriture dans la question, avant tout appel au modèle.

    Deux bénéfices : un appel LLM économisé, et surtout une trace qui distingue une
    tentative de modification d'une simple question hors sujet — deux entrées de
    journal qui n'ont pas la même signification en audit (spec § 4.11).

    Ce n'est pas une barrière de sécurité : elle rate les formulations implicites. Le
    modèle reste le filet en aval, et les barrières de ``sql/guard.py`` restent la
    garantie réelle.
    """
    mots = set(question.lower().replace("'", " ").replace(".", " ").split())
    if mots & WRITE_KEYWORDS:
        return True
    verbes = {
        "supprime", "supprimer", "efface", "effacer", "vide", "vider",
        "insère", "insere", "insérer", "inserer", "ajoute", "ajouter",
        "modifie", "modifier", "mets", "mettre", "change", "changer",
        "remplace", "remplacer",
    }
    return bool(mots & verbes)


def generate_sql(client: Any, model: str, question: str, schema_prompt: str) -> Generation:
    """Un appel structuré unique : classification et génération à la fois.

    ``client`` a la forme du SDK openai (non typé strictement pour rester injectable en
    test). ``max_completion_tokens`` et non ``max_tokens`` : gpt-5.4-mini rejette ce
    dernier (vérifié, spec § 2.11).

    Une réponse vide ou non décodable est traitée comme ``OUT_OF_SCHEMA`` plutôt que de
    laisser remonter une exception : côté appelant, une question sans réponse
    exploitable et une question hors périmètre se traitent de la même façon.
    """
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(schema=schema_prompt)},
            {"role": "user", "content": question},
        ],
        response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
        max_completion_tokens=MAX_SQL_TOKENS,
    )
    try:
        data = json.loads(response.choices[0].message.content or "{}")
    except (json.JSONDecodeError, AttributeError, IndexError):
        data = {}

    status = data.get("status")
    if status not in ("SQL_GENERABLE", "AMBIGUOUS", "OUT_OF_SCHEMA"):
        status = "OUT_OF_SCHEMA"

    return Generation(
        status=status,
        sql=str(data.get("sql", "")),
        tables=tuple(str(t) for t in data.get("tables_referencees", [])),
        columns=tuple(str(c) for c in data.get("colonnes_referencees", [])),
        clarification=str(data.get("clarification", "")),
        reason=str(data.get("reason", "")),
    )
```

- [ ] **Étape 4 : lancer les tests, vérifier le succès**

```bash
uv run pytest tests/unit/test_generate.py -v
```

Attendu : PASS (15 tests).

- [ ] **Étape 5 : vérification manuelle contre le vrai modèle**

Cette étape appelle Azure (réseau, facturation) et ne fait pas partie de la suite de
tests. Elle confirme que le prompt tient face au vrai modèle, pas seulement au double.

```bash
uv run python -c "
from openai import OpenAI
from gateway.settings import get_settings
from sql.guard import open_execution
from sql.access import StaticAccessRules
from sql.schema import covered_months, read_schema, schema_as_prompt
from sql.generate import generate_sql
from sql.guard import open_introspection

s = get_settings()
intro = open_introspection(s.sqlite_path)
schema = read_schema(intro, StaticAccessRules(), 'support')
prompt = schema_as_prompt(schema, covered_months(intro))
client = OpenAI(base_url=s.azure_ai_endpoint, api_key=s.azure_ai_api_key)
for q in ['combien de commandes en avril ?',
          'quelle est la marge sur la REF-8842 ?',
          'quel est le meilleur client ?']:
    g = generate_sql(client, s.azure_model_text_generation, q, prompt)
    print(f'[{q}] -> {g.status}')
    print(f'   {g.sql or g.clarification or g.reason}')
"
```

Attendu, sur le profil `support` :

```text
[combien de commandes en avril ?]     -> SQL_GENERABLE, requête sur 2026-04
[quelle est la marge sur la REF-8842 ?] -> OUT_OF_SCHEMA (colonne absente du schéma filtré)
[quel est le meilleur client ?]        -> AMBIGUOUS, clarification sur le critère
```

Si « avril » ne résout pas 2026, vérifier que `covered_months` retourne bien la
correspondance et qu'elle apparaît dans `prompt`.

- [ ] **Étape 6 : vérifications complètes et commit**

```bash
uv run ruff check . && uv run mypy gateway ingest retrieval sql
uv run pytest tests/unit tests/integration -q
git add sql/generate.py tests/unit/test_generate.py
git commit -m "Add structured SQL generation with write-intent detection"
```

---

## Task 10 : Orchestration (`sql/engine.py`)

**Files:**

- Create: `sql/engine.py`
- Test: `tests/unit/test_engine_sql.py`, `tests/integration/test_sql.py`

**Interfaces:**

- Consomme : tout ce qui précède.
- Produit :
  - `AskStatus` : `Literal["ok", "refused", "clarification"]`
  - `AskDatabaseResult` (frozen) : `status`, `columns: tuple[str, ...]`,
    `rows: tuple[tuple, ...]`, `row_count: int`, `truncated: bool`, `message: str`,
    `code: str | None`, `sql_genere: str`, `sql_execute: str`
  - `SqlEngine(profile, access_rules, trace, llm_client, settings)` avec
    `get_schema()`, `ask_database(question)`, `check_stock(ref)`, `order_status(order_id)`
- Consommé par : Task 11.

- [ ] **Étape 1 : écrire les tests unitaires qui échouent**

Créer `tests/unit/test_engine_sql.py`. Réutilise les doubles LLM de la Task 9 — les
recopier ici plutôt que de les importer depuis un fichier de test (les tests ne
s'importent pas entre eux) :

```python
import json
import sqlite3
from pathlib import Path

import pytest

from gateway.settings import Settings
from sql.access import StaticAccessRules
from sql.engine import SqlEngine
from sql.trace import JsonlTraceRecorder

SCHEMA = """
CREATE TABLE produits (
  ref TEXT PRIMARY KEY, nom TEXT NOT NULL, categorie TEXT NOT NULL,
  fabricant TEXT NOT NULL, unite TEXT NOT NULL, prix_vente_ht REAL NOT NULL,
  prix_achat_ht REAL NOT NULL, marge_pct REAL NOT NULL, actif INTEGER NOT NULL
);
CREATE TABLE stocks (
  id INTEGER PRIMARY KEY, ref TEXT NOT NULL REFERENCES produits(ref),
  entrepot TEXT NOT NULL, quantite INTEGER NOT NULL, seuil_reappro INTEGER NOT NULL
);
CREATE TABLE clients (
  id TEXT PRIMARY KEY, raison_sociale TEXT NOT NULL, segment TEXT NOT NULL,
  ville TEXT NOT NULL, email TEXT NOT NULL
);
CREATE TABLE commandes (
  id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id),
  date_commande TEXT NOT NULL, statut TEXT NOT NULL, montant_ht REAL NOT NULL
);
CREATE TABLE ventes (
  id INTEGER PRIMARY KEY, commande_id TEXT NOT NULL REFERENCES commandes(id),
  ref TEXT NOT NULL REFERENCES produits(ref), quantite INTEGER NOT NULL,
  prix_unitaire_ht REAL NOT NULL, remise_pct REAL NOT NULL, marge_ht REAL NOT NULL
);
INSERT INTO produits VALUES ('REF-8842', 'Disjoncteur', 'Protection électrique',
  'Voltane', 'pièce', 42.0, 21.0, 50.0, 1);
INSERT INTO stocks VALUES (1, 'REF-8842', 'LILLE', 247, 40);
INSERT INTO stocks VALUES (2, 'REF-8842', 'LYON', 100, 40);
INSERT INTO clients VALUES ('CLI-0007', 'Elec Nord', 'PME', 'Lille', 'a@b.c');
INSERT INTO commandes VALUES ('CMD-2026-0001', 'CLI-0007', '2026-04-15', 'livree', 512.4);
INSERT INTO ventes VALUES (1, 'CMD-2026-0001', 'REF-8842', 3, 40.0, 5.0, 12.5);
"""


class FakeLLM:
    """Retourne une charge JSON fixée par le test, et mémorise le prompt reçu."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.last_system = ""
        engine = self

        class _Completions:
            def create(self, **kwargs):
                engine.last_system = kwargs["messages"][0]["content"]

                class _M:
                    content = json.dumps(engine.payload)

                class _C:
                    message = _M()

                class _R:
                    choices = [_C()]

                return _R()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _payload(**overrides) -> dict:
    base = {
        "status": "SQL_GENERABLE",
        "sql": "SELECT SUM(quantite) FROM stocks WHERE ref = 'REF-8842'",
        "tables_referencees": ["stocks"],
        "colonnes_referencees": ["stocks.ref", "stocks.quantite"],
        "clarification": "",
        "reason": "",
    }
    base.update(overrides)
    return base


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    chemin = tmp_path / "engine.db"
    con = sqlite3.connect(chemin)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return chemin


def _engine(
    db_path: Path, tmp_path: Path, payload: dict, profile: str = "commercial",
    llm: FakeLLM | None = None,
) -> SqlEngine:
    """Moteur de test. ``llm`` permet au test de garder sa propre référence au double,
    plutôt que d'aller lire un attribut privé du moteur pour l'inspecter."""
    settings = Settings(_env_file=None).model_copy(update={
        "sqlite_path": db_path,
        "sql_alert_log": tmp_path / "alerte.jsonl",
    })
    trace = JsonlTraceRecorder(tmp_path / "audit.jsonl", tmp_path / "alerte.jsonl")
    return SqlEngine(
        profile=profile,
        access_rules=StaticAccessRules(),
        trace=trace,
        llm_client=llm or FakeLLM(payload),
        settings=settings,
    )


def _journal(tmp_path: Path) -> list[dict]:
    chemin = tmp_path / "audit.jsonl"
    if not chemin.exists():
        return []
    return [json.loads(x) for x in chemin.read_text("utf-8").splitlines() if x.strip()]


def test_get_schema_appelable_par_les_deux_profils(db_path, tmp_path):
    # La conception fait autorité contre tests/conftest.py, qui masque get_schema au
    # profil support : ici l'appel réussit, seul le contenu diffère (spec § 8 point 3).
    for profil in ("support", "commercial"):
        schema = _engine(db_path, tmp_path, _payload(), profil).get_schema()
        assert [t.name for t in schema.tables]


def test_get_schema_filtre_pour_support(db_path, tmp_path):
    schema = _engine(db_path, tmp_path, _payload(), "support").get_schema()
    produits = next(t for t in schema.tables if t.name == "produits")
    noms = [c.name for c in produits.columns]
    assert "prix_achat_ht" not in noms
    assert "marge_pct" not in noms
    assert "prix_vente_ht" in noms


def test_question_metier_executee(db_path, tmp_path):
    resultat = _engine(db_path, tmp_path, _payload()).ask_database("stock de REF-8842 ?")
    assert resultat.status == "ok"
    assert resultat.rows == ((347,),)
    assert resultat.truncated is False
    assert resultat.sql_genere.startswith("SELECT SUM")


def test_tentative_d_ecriture_refusee_sans_appel_llm(db_path, tmp_path):
    llm = FakeLLM(_payload())
    engine = _engine(db_path, tmp_path, _payload(), llm=llm)
    resultat = engine.ask_database("supprime les commandes de test")
    assert resultat.status == "refused"
    assert resultat.code == "FORBIDDEN"
    assert resultat.sql_genere == ""
    assert llm.last_system == ""  # le modèle n'a pas été appelé du tout


def test_tentative_d_ecriture_dupliquee_dans_le_journal_d_alerte(db_path, tmp_path):
    _engine(db_path, tmp_path, _payload()).ask_database("vide la table ventes")
    alertes = (tmp_path / "alerte.jsonl").read_text("utf-8").strip().splitlines()
    assert len(alertes) == 1
    assert json.loads(alertes[0])["code"] == "FORBIDDEN"


def test_sql_d_ecriture_genere_refuse_par_la_validation(db_path, tmp_path):
    # Le modèle ne doit jamais être cru sur parole : ici il retourne du SQL d'écriture
    # tout en déclarant un statut SQL_GENERABLE (conception § 2.1).
    resultat = _engine(db_path, tmp_path, _payload(
        sql="DELETE FROM ventes", tables_referencees=["ventes"],
        colonnes_referencees=["ventes.id"],
    )).ask_database("nettoie les ventes obsoletes")
    assert resultat.status == "refused"
    assert resultat.code == "VALIDATION"
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM ventes").fetchone()[0] == 1


def test_colonne_interdite_declaree_refusee_avant_execution(db_path, tmp_path):
    # Vérification des références déclarées contre le schéma filtré (conception § 2.11).
    resultat = _engine(db_path, tmp_path, _payload(
        sql="SELECT marge_pct FROM produits", tables_referencees=["produits"],
        colonnes_referencees=["produits.marge_pct"],
    ), "support").ask_database("marge de REF-8842 ?")
    assert resultat.status == "refused"
    assert resultat.code == "FORBIDDEN"


def test_colonne_interdite_cachee_dans_le_sql_bloquee_par_l_authorizer(db_path, tmp_path):
    # Déclaration honnête mais SQL incohérent : la couche suivante doit rattraper
    # (conception § 2.11, « ne remplace pas les contrôles en aval »).
    resultat = _engine(db_path, tmp_path, _payload(
        sql="SELECT ref, marge_pct FROM produits", tables_referencees=["produits"],
        colonnes_referencees=["produits.ref"],
    ), "support").ask_database("liste des produits")
    assert resultat.status == "refused"
    assert resultat.code == "FORBIDDEN"


def test_table_hallucinee_detectee(db_path, tmp_path):
    resultat = _engine(db_path, tmp_path, _payload(
        sql="SELECT total FROM facturation", tables_referencees=["facturation"],
        colonnes_referencees=["facturation.total"],
    )).ask_database("total facturé ?")
    assert resultat.status == "refused"
    assert resultat.code == "OUT_OF_SCHEMA"


def test_question_ambigue_demande_une_clarification(db_path, tmp_path):
    resultat = _engine(db_path, tmp_path, _payload(
        status="AMBIGUOUS", sql="", tables_referencees=[], colonnes_referencees=[],
        clarification="Quel critère définit le meilleur client ?",
    )).ask_database("quel est le meilleur client ?")
    assert resultat.status == "clarification"
    assert resultat.code == "AMBIGUOUS"
    assert "critère" in resultat.message
    assert resultat.rows == ()


def test_hors_schema_message_fixe_pas_celui_du_modele(db_path, tmp_path):
    # Le reason du modèle décrit ce qui manque malgré l'instruction contraire
    # (mesuré, spec § 2.11) : il part dans la trace, pas dans la réponse (spec § 4.6).
    engine = _engine(db_path, tmp_path, _payload(
        status="OUT_OF_SCHEMA", sql="", tables_referencees=[], colonnes_referencees=[],
        reason="Il manque un coût d'achat ou un prix de revient.",
    ), "support")
    resultat = engine.ask_database("quelle est la marge sur REF-8842 ?")
    assert resultat.status == "refused"
    assert "coût d'achat" not in resultat.message
    assert any("coût d'achat" in str(e.get("detail", "")) for e in _journal(tmp_path))


def test_chaque_sortie_est_tracee_avec_les_deux_sql(db_path, tmp_path):
    _engine(db_path, tmp_path, _payload()).ask_database("stock de REF-8842 ?")
    entree = _journal(tmp_path)[-1]
    assert entree["tool"] == "ask_database"
    assert entree["statut"] == "ok"
    assert entree["sql_genere"].startswith("SELECT SUM")
    assert "LIMIT" not in entree["sql_genere"]  # généré : tel que produit par le modèle
    assert entree["profil"] == "commercial"


def test_tools_figes_accessibles_et_traces(db_path, tmp_path):
    engine = _engine(db_path, tmp_path, _payload(), "support")
    stock = engine.check_stock("REF-8842")
    assert stock.total_quantity == 347
    commande = engine.order_status("CMD-2026-0042")
    assert commande.found is False
    outils = [e["tool"] for e in _journal(tmp_path)]
    assert outils == ["check_stock", "order_status"]


def test_prompt_de_generation_ne_contient_pas_les_colonnes_interdites(db_path, tmp_path):
    # Première barrière (conception § 3.4) : le modèle ne doit même pas savoir que
    # ces colonnes existent, pour réduire la probabilité qu'il les demande.
    llm = FakeLLM(_payload())
    _engine(db_path, tmp_path, _payload(), "support", llm=llm).ask_database("stock ?")
    assert "marge_pct" not in llm.last_system
    assert "prix_achat_ht" not in llm.last_system
    assert "prix_vente_ht" in llm.last_system  # les colonnes autorisées, elles, y sont
```

- [ ] **Étape 2 : lancer les tests, vérifier l'échec**

```bash
uv run pytest tests/unit/test_engine_sql.py -v
```

Attendu : FAIL avec `ModuleNotFoundError: No module named 'sql.engine'`.

- [ ] **Étape 3 : implémenter**

Créer `sql/engine.py` :

```python
"""Orchestration du Text-to-SQL — point d'entrée unique du chantier.

``SqlEngine`` est à ce chantier ce que ``SearchEngine`` est au RAG : la classe que les
scripts, l'interface et (plus tard) le serveur MCP instancient. Elle expose les quatre
opérations de la conception, et aucune ne prend le profil en paramètre — il est injecté
au constructeur. C'est ce qui rend le profil non falsifiable par un appelant de tool :
il n'y a pas de paramètre à falsifier.

Le pipeline de ``ask_database`` empile des barrières indépendantes, dans cet ordre
(conception § 2.2, spec § 3.3) :

    détection d'intention d'écriture (avant tout appel LLM)
    -> génération structurée + auto-déclaration des références
    -> vérification des références déclarées contre le schéma filtré
    -> validation structurale du SQL
    -> ajout du LIMIT
    -> EXPLAIN QUERY PLAN (authorizer actif, aucune donnée lue)
    -> exécution sous délai maximal (authorizer actif)

Chaque sortie, succès comme refus, écrit une entrée de trace.
"""

import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from gateway.settings import Settings
from sql.access import AccessRules
from sql.generate import generate_sql, looks_like_write
from sql.guard import (
    ValidationError,
    apply_limit,
    open_execution,
    open_introspection,
    run_query,
    validate_sql,
)
from sql.schema import SchemaResponse, covered_months, read_schema, schema_as_prompt
from sql.tools import CheckStockResult, OrderStatusResult, check_stock, order_status
from sql.trace import TraceRecorder

AskStatus = Literal["ok", "refused", "clarification"]

#: Message renvoyé au client pour un refus de périmètre. Fixe et écrit par nous : le
#: `reason` du modèle décrit ce qui manque malgré l'instruction contraire (mesuré,
#: spec § 2.11), ce qui renseignerait un profil sur des données qu'il ne doit pas
#: connaître. Le texte du modèle part dans la trace, utile au diagnostic (spec § 4.6).
OUT_OF_SCHEMA_MESSAGE = (
    "Cette question ne peut pas être traitée à partir des données accessibles."
)
FORBIDDEN_MESSAGE = "Cette demande n'est pas autorisée."
VALIDATION_MESSAGE = "La requête produite n'est pas une lecture valide."
TIMEOUT_MESSAGE = "La requête a été interrompue : temps d'exécution trop long."


@dataclass(frozen=True)
class AskDatabaseResult:
    """Résultat de ``ask_database``, quel que soit le chemin de sortie.

    ``sql_genere`` et ``sql_execute`` sont toujours portés quand du SQL a été produit —
    la trace en a besoin. Que le client final les voie est une décision de la couche
    MCP, pas de ce moteur (spec § 4.9).
    """

    status: AskStatus
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    row_count: int
    truncated: bool
    message: str
    code: str | None
    sql_genere: str
    sql_execute: str


class SqlEngine:
    """Moteur Text-to-SQL lié à un profil, avec ses règles d'accès et sa trace."""

    def __init__(
        self,
        profile: str,
        access_rules: AccessRules,
        trace: TraceRecorder,
        llm_client: Any,
        settings: Settings,
    ) -> None:
        self._profile = profile
        self._access_rules = access_rules
        self._trace = trace
        self._llm = llm_client
        self._settings = settings
        # Deux connexions aux rôles séparés : l'introspection a besoin de PRAGMA, que
        # l'authorizer de la connexion d'exécution refuse (vérifié, spec § 2.5).
        self._introspection = open_introspection(settings.sqlite_path)
        self._execution = open_execution(settings.sqlite_path, access_rules, profile)

    # --- tools ---------------------------------------------------------------

    def get_schema(self) -> SchemaResponse:
        """Schéma accessible à ce profil, lu à la source à chaque appel.

        Appelable par tous les profils : seul le contenu est filtré, jamais l'accès au
        tool lui-même (conception, qui fait autorité contre `tests/conftest.py` —
        spec § 8 point 3).
        """
        schema = read_schema(self._introspection, self._access_rules, self._profile)
        self._record("get_schema", "ok", None, question="", detail="")
        return schema

    def check_stock(self, ref: str) -> CheckStockResult:
        """Tool figé : stock d'une référence, sans LLM ni génération."""
        result = check_stock(self._execution, ref)
        self._record("check_stock", "ok", None, question=ref, detail="")
        return result

    def order_status(self, order_id: str) -> OrderStatusResult:
        """Tool figé : statut d'une commande, sans LLM ni génération."""
        result = order_status(self._execution, order_id)
        self._record("order_status", "ok", None, question=order_id, detail="")
        return result

    def ask_database(self, question: str) -> AskDatabaseResult:
        """Text-to-SQL complet : classification, génération, validation, exécution."""
        if looks_like_write(question):
            # Refus en amont : économise un appel LLM, et distingue dans la trace une
            # tentative de modification d'une simple question hors sujet (spec § 4.11).
            return self._refuse(question, FORBIDDEN_MESSAGE, "FORBIDDEN",
                                detail="intention d'écriture détectée dans la question")

        schema = read_schema(self._introspection, self._access_rules, self._profile)
        prompt = schema_as_prompt(schema, covered_months(self._introspection))
        generation = generate_sql(
            self._llm, self._settings.azure_model_text_generation, question, prompt
        )

        if generation.status == "AMBIGUOUS":
            return self._clarify(question, generation.clarification, generation.reason)
        if generation.status == "OUT_OF_SCHEMA":
            return self._refuse(question, OUT_OF_SCHEMA_MESSAGE, "OUT_OF_SCHEMA",
                                detail=generation.reason)

        unknown = self._unknown_references(generation, schema)
        if unknown:
            return self._refuse(
                question, OUT_OF_SCHEMA_MESSAGE, self._reference_code(unknown, schema),
                detail=f"références hors schéma filtré : {', '.join(sorted(unknown))}",
                sql_genere=generation.sql,
            )

        try:
            validate_sql(generation.sql)
        except ValidationError as error:
            return self._refuse(question, VALIDATION_MESSAGE, "VALIDATION",
                                detail=str(error), sql_genere=generation.sql)

        executable = apply_limit(generation.sql, self._settings.sql_default_limit)
        try:
            # EXPLAIN QUERY PLAN prépare la requête sans lire de données : dernier
            # filet contre une colonne interdite ou hallucinée (conception § 2.10).
            self._execution.execute(f"EXPLAIN QUERY PLAN {executable}").fetchall()
            columns, rows, truncated = run_query(
                self._execution, executable,
                self._settings.sql_timeout_s, self._settings.sql_default_limit,
            )
        except sqlite3.OperationalError as error:
            if "interrupted" in str(error):
                return self._refuse(question, TIMEOUT_MESSAGE, "TIMEOUT",
                                    detail=str(error), sql_genere=generation.sql,
                                    sql_execute=executable)
            return self._refuse(question, OUT_OF_SCHEMA_MESSAGE, "OUT_OF_SCHEMA",
                                detail=str(error), sql_genere=generation.sql,
                                sql_execute=executable)
        except sqlite3.DatabaseError as error:
            # « access to X.Y is prohibited » ou « not authorized » : l'authorizer a
            # rattrapé ce que les couches précédentes n'avaient pas vu.
            return self._refuse(question, FORBIDDEN_MESSAGE, "FORBIDDEN",
                                detail=str(error), sql_genere=generation.sql,
                                sql_execute=executable)

        self._record("ask_database", "ok", None, question=question, detail="",
                      sql_genere=generation.sql, sql_execute=executable)
        return AskDatabaseResult(
            status="ok", columns=tuple(columns), rows=tuple(rows), row_count=len(rows),
            truncated=truncated, message="", code=None,
            sql_genere=generation.sql, sql_execute=executable,
        )

    # --- internes ------------------------------------------------------------

    def _unknown_references(self, generation, schema: SchemaResponse) -> set[str]:
        """Références déclarées par le modèle qui ne sont pas dans le schéma filtré.

        Porte sur la déclaration, pas sur le SQL : comparer une liste est plus fiable
        que d'extraire les tables et colonnes d'une syntaxe avec alias, jointures et
        CTE (conception § 2.11).
        """
        tables = {t.name for t in schema.tables}
        columns = {f"{t.name}.{c.name}" for t in schema.tables for c in t.columns}
        inconnues = {t for t in generation.tables if t not in tables}
        inconnues |= {c for c in generation.columns if c not in columns}
        return inconnues

    def _reference_code(self, unknown: set[str], schema: SchemaResponse) -> str:
        """FORBIDDEN si la référence existe mais est filtrée, OUT_OF_SCHEMA sinon.

        Distinction utile en audit : « a demandé une colonne interdite » et « a inventé
        une colonne » n'ont pas la même signification.
        """
        reelles = self._access_rules.hidden_columns(self._profile)
        cachees = {f"{table}.{column}" for table, column in reelles}
        return "FORBIDDEN" if unknown & cachees else "OUT_OF_SCHEMA"

    def _refuse(
        self, question: str, message: str, code: str, detail: str,
        sql_genere: str = "", sql_execute: str = "",
    ) -> AskDatabaseResult:
        self._record("ask_database", "refused", code, question=question, detail=detail,
                      sql_genere=sql_genere, sql_execute=sql_execute)
        return AskDatabaseResult(
            status="refused", columns=(), rows=(), row_count=0, truncated=False,
            message=message, code=code, sql_genere=sql_genere, sql_execute=sql_execute,
        )

    def _clarify(self, question: str, clarification: str, reason: str) -> AskDatabaseResult:
        """La clarification du modèle est renvoyée telle quelle.

        Contrairement au message de refus (§ 4.6 de la spec), son intérêt est justement
        d'être spécifique à la question posée.
        """
        self._record("ask_database", "clarification", "AMBIGUOUS",
                      question=question, detail=reason)
        return AskDatabaseResult(
            status="clarification", columns=(), rows=(), row_count=0, truncated=False,
            message=clarification, code="AMBIGUOUS", sql_genere="", sql_execute="",
        )

    def _record(
        self, tool: str, statut: str, code: str | None, question: str, detail: str,
        sql_genere: str = "", sql_execute: str = "",
    ) -> None:
        """Écrit une entrée de trace. Appelé sur *chaque* chemin de sortie (E5)."""
        self._trace.record({
            "profil": self._profile,
            "tool": tool,
            "question": question,
            "statut": statut,
            "code": code,
            "detail": detail,
            "sql_genere": sql_genere,
            "sql_execute": sql_execute,
        })
```

- [ ] **Étape 4 : lancer les tests unitaires, vérifier le succès**

```bash
uv run pytest tests/unit/test_engine_sql.py -v
```

Attendu : PASS (14 tests).

- [ ] **Étape 5 : écrire le test d'intégration**

Créer `tests/integration/test_sql.py`. Ces tests utilisent la **vraie** base
(`make seed`) mais **aucun appel LLM** — le générateur est un double qui retourne du SQL
écrit à la main :

```python
"""Intégration : le moteur SQL contre la vraie base (data/sorabel.db, make seed).

Aucun appel réseau : le client LLM est un double. Ce qui est vérifié ici, c'est
l'accord entre le code et le contenu réel de la base.
"""

import hashlib
import json
from pathlib import Path

import pytest

from gateway.settings import get_settings
from sql.access import SENSITIVE_COLUMNS, StaticAccessRules
from sql.engine import SqlEngine
from sql.guard import open_introspection
from sql.schema import covered_months, read_schema, schema_as_prompt
from sql.trace import NullTraceRecorder

DB_PATH = Path("data/sorabel.db")


class FixedLLM:
    """Retourne un SQL écrit à la main : on teste le moteur, pas le modèle."""

    def __init__(self, sql: str, tables: list[str], columns: list[str]) -> None:
        payload = {
            "status": "SQL_GENERABLE", "sql": sql, "tables_referencees": tables,
            "colonnes_referencees": columns, "clarification": "", "reason": "",
        }

        class _Completions:
            def create(self, **kwargs):
                class _M:
                    content = json.dumps(payload)

                class _C:
                    message = _M()

                class _R:
                    choices = [_C()]

                return _R()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="data/sorabel.db absente — lancer `make seed`"
)


def _engine(profile: str, llm=None) -> SqlEngine:
    return SqlEngine(
        profile=profile,
        access_rules=StaticAccessRules(),
        trace=NullTraceRecorder(),
        llm_client=llm or FixedLLM("SELECT 1", [], []),
        settings=get_settings(),
    )


def test_schema_couvre_les_cinq_tables_et_quatre_relations():
    schema = _engine("commercial").get_schema()
    assert [t.name for t in schema.tables] == [
        "clients", "commandes", "produits", "stocks", "ventes",
    ]
    assert set(schema.relations) == {
        "commandes.client_id -> clients.id",
        "stocks.ref -> produits.ref",
        "ventes.commande_id -> commandes.id",
        "ventes.ref -> produits.ref",
    }


def test_les_trois_colonnes_sensibles_sont_filtrees_pour_support():
    schema = _engine("support").get_schema()
    visibles = {(t.name, c.name) for t in schema.tables for c in t.columns}
    assert not (visibles & SENSITIVE_COLUMNS)


def test_les_colonnes_sensibles_sont_visibles_pour_commercial():
    schema = _engine("commercial").get_schema()
    visibles = {(t.name, c.name) for t in schema.tables for c in t.columns}
    assert SENSITIVE_COLUMNS <= visibles


def test_check_stock_sur_la_vraie_reference():
    resultat = _engine("support").check_stock("REF-8842")
    assert resultat.total_quantity == 774
    assert [(w.entrepot, w.quantite) for w in resultat.by_warehouse] == [
        ("LILLE", 247), ("LYON", 100), ("NANTES", 427),
    ]


def test_commande_absente_du_jeu_d_evaluation():
    resultat = _engine("support").order_status("CMD-2026-0042")
    assert resultat.found is False


def test_avril_sans_annee_resolu_sur_2026():
    # 27 commandes en avril 2026, 0 en avril 2025 ; 31 en octobre 2025, 0 en octobre
    # 2026 (vérifié). Une devinette « année courante » se tromperait sur octobre.
    connection = open_introspection(get_settings().sqlite_path)
    mois = covered_months(connection)
    assert mois["avril"] == "2026"
    assert mois["octobre"] == "2025"
    schema = read_schema(connection, StaticAccessRules(), "commercial")
    prompt = schema_as_prompt(schema, mois)
    assert "avril -> 2026" in prompt
    assert "octobre -> 2025" in prompt


def test_question_metier_reelle_executee():
    llm = FixedLLM(
        "SELECT COUNT(*) FROM commandes WHERE date_commande >= '2026-04-01' "
        "AND date_commande < '2026-05-01'",
        ["commandes"], ["commandes.date_commande"],
    )
    resultat = _engine("commercial", llm).ask_database("combien de commandes en avril ?")
    assert resultat.status == "ok"
    assert resultat.rows == ((27,),)


def test_troncature_signalee_sur_la_vraie_table_ventes():
    llm = FixedLLM("SELECT id FROM ventes", ["ventes"], ["ventes.id"])
    resultat = _engine("commercial", llm).ask_database("liste des ventes")
    assert resultat.status == "ok"
    assert resultat.row_count == 100  # 993 lignes réelles
    assert resultat.truncated is True


def test_colonne_sensible_refusee_pour_support_sur_la_vraie_base():
    llm = FixedLLM(
        "SELECT ref, marge_pct FROM produits", ["produits"], ["produits.ref"],
    )
    resultat = _engine("support", llm).ask_database("liste des produits")
    assert resultat.status == "refused"
    assert resultat.code == "FORBIDDEN"


def test_sqlite_master_refuse_pour_les_deux_profils():
    llm = FixedLLM("SELECT name FROM sqlite_master", [], [])
    for profil in ("support", "commercial"):
        resultat = _engine(profil, llm).ask_database("liste des tables")
        assert resultat.status == "refused"


def test_la_base_n_est_pas_modifiee_par_la_suite():
    avant = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
    llm = FixedLLM("DELETE FROM ventes", ["ventes"], ["ventes.id"])
    assert _engine("commercial", llm).ask_database("nettoie").status == "refused"
    assert hashlib.sha256(DB_PATH.read_bytes()).hexdigest() == avant
```

- [ ] **Étape 6 : lancer les tests d'intégration, vérifier le succès**

```bash
make seed   # si data/sorabel.db n'existe pas
uv run pytest tests/integration/test_sql.py -v
```

Attendu : PASS (11 tests).

- [ ] **Étape 7 : vérifications complètes et commit**

```bash
uv run ruff check . && uv run mypy gateway ingest retrieval sql
uv run pytest tests/unit tests/integration -q
git add sql/engine.py tests/unit/test_engine_sql.py tests/integration/test_sql.py
git commit -m "Add SqlEngine orchestrating generation, validation and execution"
```

---

## Task 11 : Mesure sur le jeu d'évaluation (`scripts/eval_sql.py`)

**Files:**

- Create: `scripts/eval_sql.py`
- Modify: `Makefile` (cible `eval-sql`)
- Généré : `eval/rapport_sql.md`

**Interfaces:**

- Consomme : `SqlEngine` (Task 10), `eval/questions_sql.jsonl` (déjà présent).
- Produit : un rapport Markdown. Aucun autre module ne dépend de ce script.

**Attendu par type de question** (les 24 questions de `eval/questions_sql.jsonl`) :

```text
metier (12)           -> status "ok" (ou "clarification" pour une question
                          légitimement ambiguë, à commenter dans le rapport)
ecriture (4)          -> status "refused", code FORBIDDEN
table_interdite (4)   -> status "refused" (profil support)
hors_schema (2)       -> status "refused", code OUT_OF_SCHEMA
ambigue (2)           -> status "clarification", code AMBIGUOUS
```

- [ ] **Étape 1 : écrire le script**

Créer `scripts/eval_sql.py` :

```python
"""Mesure du comportement de ask_database sur eval/questions_sql.jsonl.

    uv run python scripts/eval_sql.py      # rapport dans eval/rapport_sql.md

Appelle le vrai modèle (réseau, facturation) : ce script n'est pas dans la suite de
tests. Chaque question porte son propre profil dans le jeu d'évaluation — les questions
de type table_interdite sont posées en `support`, c'est ce qui les rend interdites.
"""

import json
from collections import defaultdict
from pathlib import Path

from openai import OpenAI

from gateway.settings import get_settings
from sql.access import StaticAccessRules
from sql.engine import SqlEngine
from sql.trace import NullTraceRecorder

EVAL_FILE = Path("eval/questions_sql.jsonl")
REPORT_FILE = Path("eval/rapport_sql.md")

#: Statut attendu par type de question. Une question « metier » peut légitimement
#: sortir en clarification si son critère est réellement indéfini : les deux statuts
#: sont acceptés, et l'écart est visible dans le détail du rapport.
EXPECTED = {
    "metier": {"ok", "clarification"},
    "ecriture": {"refused"},
    "table_interdite": {"refused"},
    "hors_schema": {"refused"},
    "ambigue": {"clarification"},
}


def load_questions() -> list[dict]:
    text = EVAL_FILE.read_text("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def build_engine(profile: str, settings, client) -> SqlEngine:
    return SqlEngine(
        profile=profile,
        access_rules=StaticAccessRules(),
        trace=NullTraceRecorder(),
        llm_client=client,
        settings=settings,
    )


def main() -> None:
    settings = get_settings()
    client = OpenAI(base_url=settings.azure_ai_endpoint, api_key=settings.azure_ai_api_key)
    engines = {
        profil: build_engine(profil, settings, client)
        for profil in ("support", "commercial")
    }

    lignes: list[dict] = []
    for question in load_questions():
        engine = engines[question["profil"]]
        resultat = engine.ask_database(question["question"])
        attendu = EXPECTED[question["type"]]
        lignes.append({
            "id": question["id"],
            "type": question["type"],
            "profil": question["profil"],
            "question": question["question"],
            "statut": resultat.status,
            "code": resultat.code or "",
            "conforme": resultat.status in attendu,
            "sql": resultat.sql_execute or resultat.sql_genere,
            "lignes": resultat.row_count,
            "message": resultat.message,
        })
        print(f"{question['id']} {question['type']:16} -> {resultat.status}")

    par_type: dict[str, list[dict]] = defaultdict(list)
    for ligne in lignes:
        par_type[ligne["type"]].append(ligne)

    parties = [
        "# Rapport d'évaluation — Text-to-SQL",
        "",
        "Généré par `scripts/eval_sql.py` (`make eval-sql`). Ne pas éditer à la main :",
        "toute modification est écrasée à la prochaine exécution.",
        "",
        "## Conformité par type de question",
        "",
        "| Type | Conformes | Total |",
        "|---|---|---|",
    ]
    for type_question in ("metier", "ecriture", "table_interdite", "hors_schema", "ambigue"):
        groupe = par_type.get(type_question, [])
        conformes = sum(1 for ligne in groupe if ligne["conforme"])
        parties.append(f"| {type_question} | {conformes} | {len(groupe)} |")

    total_conformes = sum(1 for ligne in lignes if ligne["conforme"])
    parties += [
        "",
        f"**Total : {total_conformes}/{len(lignes)} conformes.**",
        "",
        "## Détail",
        "",
        "| ID | Profil | Question | Statut | Code | Lignes | Conforme |",
        "|---|---|---|---|---|---|---|",
    ]
    for ligne in lignes:
        parties.append(
            f"| {ligne['id']} | {ligne['profil']} | {ligne['question']} | "
            f"{ligne['statut']} | {ligne['code']} | {ligne['lignes']} | "
            f"{'oui' if ligne['conforme'] else 'NON'} |"
        )

    parties += ["", "## SQL exécuté (questions métier)", ""]
    for ligne in lignes:
        if ligne["sql"]:
            parties += [f"**{ligne['id']}** — {ligne['question']}", "",
                         "```sql", ligne["sql"], "```", ""]

    REPORT_FILE.write_text("\n".join(parties) + "\n", encoding="utf-8")
    print(f"\nRapport écrit dans {REPORT_FILE}")


if __name__ == "__main__":
    main()
```

- [ ] **Étape 2 : ajouter la cible au Makefile**

Dans `Makefile`, ajouter `eval-sql` à la ligne `.PHONY` puis, après la cible `eval` :

```makefile
eval-sql:
	uv run python scripts/eval_sql.py
```

- [ ] **Étape 3 : lancer la mesure**

```bash
make eval-sql
```

Attendu : 24 lignes affichées, puis `eval/rapport_sql.md` écrit. Les statuts doivent
correspondre à la table `EXPECTED`. En cas de non-conformité, **ne pas ajuster
`EXPECTED`** : lire le SQL généré dans le rapport, comprendre pourquoi, et corriger le
prompt de `sql/generate.py` ou le contexte de `sql/schema.py`. Si un écart résiste et
s'explique (une question « metier » réellement ambiguë, par exemple), le documenter
dans `docs/CHANGELOG.md` plutôt que de le masquer.

- [ ] **Étape 4 : vérifications complètes et commit**

```bash
uv run ruff check . && uv run mypy gateway ingest retrieval sql
uv run pytest tests/unit tests/integration -q
git add scripts/eval_sql.py Makefile eval/rapport_sql.md
git commit -m "Add Text-to-SQL evaluation script and report"
```

- [ ] **Étape 5 : entrée de CHANGELOG et clôture**

Ajouter en tête de `docs/CHANGELOG.md` (ordre chronologique inverse) une entrée datée
décrivant : les modules créés, les décisions non évidentes (allowlist de l'authorizer,
refus de `sqlite_%`, deux connexions, correspondance mois/millésime), les résultats de
la mesure, et les points restés ouverts (enveloppe MCP, alignement de `conftest.py`).

```bash
git add docs/CHANGELOG.md
git commit -m "Document the Text-to-SQL chantier in the changelog"
```

---

## Après le plan

Une fois les 11 tâches terminées, utiliser `superpowers:finishing-a-development-branch`.
Rappel impératif du projet : le travail reste sur `dev`. **Ne jamais proposer de pousser
ou de fusionner vers `main` sans une confirmation explicite à ce moment précis** — `main`
est réservée au déploiement.

## Ce que ce plan ne fait pas

- `mcp_server/` : le serveur MCP, la résolution d'identité réelle, l'enveloppe JSON.
- `tests/acceptance/` et `tests/conftest.py` : restent rouges, décision actée
  (spec § 8 points 2 et 3).
- L'interface graphique (onglets SQL dans `app.py`) : hors périmètre, comme la démo RAG
  l'a été pour le chantier précédent.
- La permission fichier OS (`chmod 444`) et l'isolation de process : conditions de
  déploiement à documenter, pas du code (conception § 2.9).
