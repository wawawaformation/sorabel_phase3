# Spécification — Serveur MCP, matrice d'accès et journal unique

> Chantier 3 du brief (`brief/brief.md` § « Chantier 3 — Concevoir l'exposition MCP et la
> matrice d'accès »). La conception validée en amont vit dans `conception/3_MCP/` :
> `questions_reponses_mcp.md` (raisonnement complet), `catalogue_tools_mcp.md` et
> `journal_mcp.md` (fiches de synthèse). Cette spec ne les rejoue pas : elle assemble les
> moteurs déjà construits (`retrieval/`, `sql/`) derrière un serveur MCP, et corrige les
> quelques points où la conception ou le scaffold de tests fourni se sont révélés inexacts
> une fois relus contre le code réel des chantiers 1 et 2.

## 1. Objectif

Exposer les 8 tools déjà conçus (4 RAG, 4 SQL) à travers un serveur MCP unique
(`mcp_server.server`, transport stdio), avec :

- résolution du profil côté serveur, jamais un paramètre de tool (E4) ;
- application d'une matrice d'accès (E4) ;
- journalisation unique de tout appel, autorisé ou refusé (E5) ;
- un contrat d'erreur commun (`isError`, `_meta["sorabel/error_code"]`) par-dessus les
  états déjà produits par `retrieval/` et `sql/`.

Contraintes héritées, non rediscutées ici : deux profils (`support`, `commercial`), profil
résolu par variable d'environnement pour la démo, `sql/` et `retrieval/` inchangés (chacun
expose déjà les `Protocol` d'injection nécessaires : `AccessRules`, `TraceRecorder`).

Hors périmètre : OAuth 2.0/OIDC réel (voir § 4.2), interface graphique détaillée (§ 6).

---

## 2. Écarts trouvés entre conception, scaffold de tests et code réel

Le scaffold fourni (`tests/acceptance/*.py`, `tests/conftest.py`) date du même commit qu'un
fichier `docs/cadrage_dsi.md` déclaré écarté (`CHANGELOG.md`, 2026-09-01 : oubli de
suppression, confirmé par le formateur). Une partie du scaffold encode pourtant exactement
la matrice de ce document écarté, jamais relue depuis à la lumière du code réel. Quatre
points ont été vérifiés et tranchés :

| # | Point | Ce que dit le scaffold fourni | Ce qui fait foi | Décision |
|---|---|---|---|---|
| 1 | `get_schema` pour `support` | Refusé (`TOOLS_BY_PROFILE` exclut le tool) | `sql/engine.py:102-105` (docstring déjà écrit au chantier 2) : *« Appelable par tous les profils : seul le contenu est filtré, jamais l'accès au tool lui-même — la conception fait autorité contre `tests/conftest.py` »* | Accessible aux deux profils, colonnes sensibles filtrées dans le résultat |
| 2 | Clés du journal | Anglaises (`profile`, `status`, `arguments`) | `sql/trace.py` + `journal_mcp.md` : françaises (`profil`, `statut`, `code`, `motif`, `detail`, `identity`), déjà utilisées par `sql/engine.py._record` | Clés françaises ; tests adaptés |
| 3 | SQL généré dans le payload client (`ask_database`) | Exigé (`payload["sql"]`), et le brief officiel (`brief.md:194`) le demande aussi | `conception/3_MCP/catalogue_tools_mcp.md:28` : jamais renvoyé, tracé seulement | **Tracé seulement** (choix assumé malgré le brief et le test fourni — voir § 4.1) ; test adapté |
| 4 | Restriction de collection RAG par profil | Non testé directement | `docs/spec_retrieval.md:18-20` : *« le RAG est ouvert aux deux profils — décision confirmée par le formateur »* | Aucune restriction de collection ; seule restriction réelle du système = 3 colonnes SQL (`sql/access.py:SENSITIVE_COLUMNS`) |

Conséquence du point 4 : avec le point 1, **aucun tool n'est jamais refusé dans son
intégralité** pour un profil dans ce MVP — toute restriction est au niveau donnée (colonnes
SQL). Les deux tests qui présupposaient un refus de tool entier
(`test_matrice_d_acces_respectee`, `test_refus_message_clair_et_journalise`) sont réécrits
pour démontrer un refus donnée à la place (§ 5).

---

## 3. Architecture

```text
Client MCP (stdio, un process par profil)
        │  SORABEL_PROFILE=support|commercial (env, résolu au démarrage du process)
        ▼
mcp_server/server.py                    point d'entrée `python -m mcp_server.server`
        │
        ├─ mcp_server/identity.py       IdentityResolver (Protocol) → profil
        ├─ mcp_server/access.py         charge matrice_acces.yaml → AccessRules (sql/)
        ├─ mcp_server/envelope.py       état interne moteur → CallToolResult + JSON texte
        ├─ (réutilise) sql/trace.py     JsonlTraceRecorder, un seul logs/mcp_audit.jsonl
        │
        ├─ 4 tools RAG  → retrieval/engine.py::SearchEngine (inchangé)
        └─ 4 tools SQL  → sql/engine.py::SqlEngine (inchangé)
```

Aucun nouveau moteur métier. Le chantier 3 est un chantier d'assemblage : `retrieval/` et
`sql/` sont déjà conçus pour recevoir cette injection sans modification.

### 3.1 Modules

```text
mcp_server/
├── __init__.py
├── matrice_acces.yaml   déclaration humaine profils × tools × collections × colonnes
├── access.py            charge le YAML, implémente sql.access.AccessRules
├── identity.py          IdentityResolver (Protocol) + EnvVarIdentityResolver (défaut)
├── envelope.py          mapping état moteur -> {status, payload, message} + isError/_meta
├── catalogue.py         définitions des 8 tools (nom, schema JSON, description, _meta roles)
└── server.py            construit SearchEngine/SqlEngine par profil, déclare les tools MCP,
                          Server Instructions, branche envelope + journal
```

### 3.2 `matrice_acces.yaml`

Reflète l'état réel du système (§ 2, point 4) : les deux profils ont le même catalogue de
tools et les mêmes collections RAG ; seules les colonnes SQL diffèrent. Le fichier reste une
structure complète (pas seulement les colonnes) pour rester lisible comme la matrice de
conception et extensible si une vraie restriction de tool/collection apparaît plus tard —
mais aucune case n'est inventée pour combler une case vide.

```yaml
profiles:
  support:
    tools: [answer_question, search_docs, get_document, list_sources,
            ask_database, get_schema, check_stock, order_status]
    rag_collections: [fiches_techniques, notices, procedures_sav, notes]
    sql_hidden_columns:
      - [produits, prix_achat_ht]
      - [produits, marge_pct]
      - [ventes, marge_ht]
  commercial:
    tools: [answer_question, search_docs, get_document, list_sources,
            ask_database, get_schema, check_stock, order_status]
    rag_collections: [fiches_techniques, notices, procedures_sav, notes]
    sql_hidden_columns: []
```

`mcp_server/access.py` charge ce fichier une fois au démarrage et expose :

```python
class YamlAccessRules:  # implémente sql.access.AccessRules
    def hidden_columns(self, profile: str) -> frozenset[tuple[str, str]]: ...
    def allowed_tools(self, profile: str) -> frozenset[str]: ...       # informatif (§ 3.4)
    def allowed_collections(self, profile: str) -> frozenset[str]: ... # = toutes, aujourd'hui
```

`sql/` continue de recevoir uniquement `hidden_columns` (son `Protocol` existant) : la
matrice ne remplace pas `sql/access.py`, elle en devient la source déclarative — un seul
endroit à changer pour faire évoluer la règle.

### 3.3 Identité (§ 4.2 pour la justification)

```python
class IdentityResolver(Protocol):
    def resolve(self) -> str: ...  # "support" | "commercial"

class EnvVarIdentityResolver:
    def resolve(self) -> str:
        profile = os.environ.get("SORABEL_PROFILE", "support")
        if profile not in PROFILES:
            raise ValueError(f"profil inconnu : {profile!r}")
        return profile
```

### 3.4 `_meta["sorabel/roles"]` généré, pas maintenu à la main

Comme prévu en conception (`questions_reponses_mcp.md` § 2.4), `catalogue.py` génère
`_meta["sorabel/roles"]` de chaque tool depuis `YamlAccessRules.allowed_tools(...)`.
Aujourd'hui les deux profils ayant les mêmes tools, chaque entrée porte
`["commercial", "support"]` — informatif uniquement (§ 2.2 de la conception : « découvrir un
tool ne signifie pas être autorisé à l'exécuter » ; ici, de toute façon, tout le monde l'est).

---

## 4. Décisions

### 4.1 `ask_database` ne renvoie pas le SQL au client, malgré le brief et le test fourni

Le brief (`brief.md:194`, critère Gherkin) et `tests/acceptance/test_sql.py` attendent
`payload["sql"]`. La conception (`catalogue_tools_mcp.md:28`) et `docs/spec_sql.md` § 4.9
avaient laissé le choix ouvert au chantier 3, en notant que le moteur expose de toute façon
`sql_genere`/`sql_execute` en interne pour la trace. Décision assumée : le payload client ne
porte pas le SQL ; seul le journal le conserve (déjà le cas, `sql/engine.py._record`). Le
test fourni est adapté en conséquence (§ 5) — pas de citation de ligne du brief supplémentaire
ici, la tension est déjà documentée dans `docs/spec_sql.md` § 2.13/4.9.

### 4.2 Identité : `SORABEL_PROFILE` seul, OIDC documenté mais non codé

`conception/3_MCP/questions_reponses_mcp.md` § 1.5 décrit une architecture cible OAuth
2.0/OIDC (JWT validé localement contre un JWKS caché, IdP interchangeable, Keycloak comme
candidat local). Ni le brief (E1–E6) ni le contrat d'intégration imposé par
`tests/conftest.py` (`gateway_session` : un process par profil, profil lu dans
`SORABEL_PROFILE`) n'exigent qu'un IdP soit réellement branché pour la démo. Décision :
implémenter uniquement `EnvVarIdentityResolver`, derrière le `Protocol` `IdentityResolver` —
même patron que `AccessRules`/`TraceRecorder` dans `sql/` — pour qu'un vrai IdP soit
substituable plus tard sans toucher `mcp_server/server.py`. L'architecture OIDC reste
documentée comme cible (renvoi vers la conception), non implémentée : hors périmètre des 6
jours, non demandée par le brief.

### 4.3 Aucun refus de tool entier : la matrice ne restreint que des colonnes SQL

Voir § 2, points 1 et 4. `mcp_server/server.py` n'ajoute donc pas de contrôle RBAC
« tool interdit » générique pour le MVP — l'unique barrière data-level (`hidden_columns`)
suffit à couvrir E4/E5 telles que vérifiées par les tests adaptés (§ 5). Si un besoin
métier réel de restriction de tool apparaît, `YamlAccessRules.allowed_tools` porte déjà la
structure pour l'exprimer sans re-concevoir la matrice.

### 4.4 Enveloppe double : native MCP + JSON texte

`CallToolResult.isError` (bool) et `_meta["sorabel/error_code"]` (un des 4 codes minimums)
portent le contrat MCP natif de la conception. Le premier élément de `content` est un texte
JSON `{"status", "payload", "message"}` (vocabulaire imposé par `tests/conftest.py` :
`ok | refused | clarification | hors_corpus | error`), pour rester compatible avec le
contrat d'intégration fourni sans dupliquer la sémantique — `isError` et `status != "ok"`
sont toujours synchrones.

### 4.5 Journal : réutilisation directe de `sql/trace.py`, pas de nouveau module

`JsonlTraceRecorder` est déjà générique (`dict[str, object]`) et déjà utilisé par
`SqlEngine`. `mcp_server/server.py` instancie un seul `JsonlTraceRecorder` (chemin
`logs/mcp_audit.jsonl`, configurable via `GATEWAY_JOURNAL` — contrat scaffold), l'injecte
dans `SqlEngine`, et l'appelle lui-même pour les 4 tools RAG (`retrieval/` n'a pas encore
cette injection — ajout minimal dans `mcp_server/server.py`, pas dans `retrieval/`, pour ne
pas modifier un chantier terminé pour un besoin du suivant). Un appel RBAC-only n'existe pas
(§ 4.3) : pas d'entrée `statut="forbidden"` dans ce MVP.

---

## 5. Tests d'acceptance : adaptations

Les tests eux-mêmes ne sont pas remis en cause dans leur intention (E1–E5 vérifiées en
boîte noire) — seuls les points identifiés en § 2 sont corrigés.

- **`tests/conftest.py`** : `TOOLS_BY_PROFILE` devient identique pour les deux profils
  (§ 2, point 4) ; `read_journal`/assertions sur les entrées utilisent les clés françaises.
- **`tests/acceptance/test_mcp.py`** :
  - `test_matrice_d_acces_respectee` : remplacé par une vérification de filtrage donnée —
    `get_schema` en `support` ne contient aucune des 3 colonnes sensibles, `commercial` les a
    toutes.
  - `test_refus_message_clair_et_journalise` : remplacé par le refus déjà couvert côté SQL
    (question sur la marge en `support` via `ask_database`) — refus explicite, journalisé,
    avec message clair. `get_schema` n'est plus l'exemple de refus.
  - Les deux autres tests (`test_briques_du_rag_utilisables_separement`,
    `test_journal_exhaustif_autorises_et_refuses`) restent valides tels quels une fois les
    clés du journal corrigées.
- **`tests/acceptance/test_sql.py`** : `test_ask_database_repond_et_montre_sa_requete`
  n'asserte plus `payload["sql"]`, seulement le résultat (`rows`).
- **`tests/acceptance/test_rag.py`** : aucun changement (pas concerné par les écarts § 2).

---

## 6. Interface graphique

Nouvelle appli Streamlit (`app_gateway.py`), consommant le vrai serveur MCP via un client
stdio (même mécanique que `scripts/mcp_client.py`) — pas d'appel direct à `SearchEngine`/
`SqlEngine`. Sélecteur de profil (`support`/`commercial`) qui relance une session avec le bon
`SORABEL_PROFILE`. Détail d'implémentation laissé au plan.

---

## 7. Tests (niveaux)

- **Unitaire** : `mcp_server/access.py` (chargement YAML, `hidden_columns` cohérent avec
  `sql/access.py:SENSITIVE_COLUMNS`), `mcp_server/identity.py` (profil valide/invalide/
  défaut), `mcp_server/envelope.py` (mapping état → enveloppe pour chaque code d'erreur).
- **Intégration** : `mcp_server/server.py` avec de vrais `SearchEngine`/`SqlEngine` sur les
  données de test, sans passer par le protocole stdio.
- **Acceptance** : suite fournie et adaptée (§ 5), seule à parler stdio/MCP réel.
