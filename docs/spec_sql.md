# Spécification — Text-to-SQL, tools figés et barrières de lecture seule

> Chantier 2 du brief (`brief/brief.md` § « Chantier Text-to-SQL »). La conception
> valide en amont vit dans `conception/2_text-to-sql/` : `tools_sql_mcp.md` (contrats
> des tools) et `questions_reponses_text-to-sql.md` (raisonnement, ~2200 lignes). Cette
> spec ne les rejoue pas : elle traduit ces décisions en architecture implémentable, et
> ajoute ce qui a été vérifié empiriquement aujourd'hui contre la vraie base et le vrai
> modèle — dont deux failles que la conception n'avait pas anticipées (§ 2.4, § 2.5).

## 1. Objectif

Exposer les données de `sorabel.db` à travers quatre opérations, chacune correspondant à
un tool MCP de la conception :

| Opération | Nature | LLM ? |
|---|---|---|
| `get_schema()` | schéma filtré selon le profil | non |
| `ask_database(question)` | Text-to-SQL génératif, validé, exécuté | oui |
| `check_stock(ref)` | SQL figé paramétré | non |
| `order_status(order_id)` | SQL figé paramétré | non |

Contraintes structurantes, héritées de la conception et non rediscutées ici :

- **Lecture seule garantie par cumul de barrières indépendantes**, aucune n'étant
  considérée suffisante seule (E3).
- **Périmètre par profil** : les colonnes sensibles n'existent pas pour `support`, ni
  dans le schéma présenté, ni dans le SQL accepté, ni dans le résultat (E4).
- **`sorabel.db` n'est jamais modifié** — ni données, ni schéma, ni moteur.
- **Le SQL généré et le SQL exécuté sont toujours exposés à l'appelant du moteur**, parce
  que la trace en a besoin. Que le *client final* les voie ou non est une décision de la
  couche MCP, pas de ce chantier — et le test d'acceptance fourni l'exige (§ 2.13).
- **Le profil n'est jamais un argument de tool** — résolu côté serveur (E4).

Hors périmètre de cette spec : le serveur MCP lui-même (chantier suivant), le journal
`logs/mcp_audit.jsonl` en tant que composant partagé (voir § 4.7), l'interface graphique.

---

## 2. Faits vérifiés aujourd'hui

Tout ce qui suit a été exécuté contre la vraie base (sur copie — l'originale n'est jamais
ouverte en écriture) et contre le vrai déploiement Azure. Les scripts de vérification sont
jetables, mais chaque affirmation ci-dessous vient d'une sortie réelle, pas de la
documentation SQLite.

### 2.1 État réel de `sorabel.db`

```text
tables            produits, stocks, clients, commandes, ventes   (+ sqlite_sequence)
volumes           120 / 312 / 60 / 340 / 993 lignes
plage de dates    commandes du 2025-09-04 au 2026-08-19
statuts           annulee, en_attente, expediee, livree, preparee
entrepôts         LILLE, LYON, NANTES
segments clients  artisan, PME, grand compte, collectivité
unités produits   pièce, conditionnement
CMD-2026-0042     0 ligne — la commande du jeu d'éval n'existe effectivement pas
REF-8842          stocks LILLE 247 / LYON 100 / NANTES 427  (total 774)
```

Le fichier fourni est `brief/data/data/sorabel.db`. Il doit être copié dans
`data/sorabel.db` du dépôt d'implémentation (même logique que le corpus RAG : les données
arrivent en phase de développement).

### 2.2 Le schéma **et** les relations sont introspectables

`PRAGMA table_info(<table>)` retourne `(cid, name, type, notnull, default, pk)` — de quoi
construire la structure sans jamais la figer en dur :

```text
(0, 'ref', 'TEXT', 0, None, 1)
(5, 'prix_vente_ht', 'REAL', 1, None, 0)
```

`PRAGMA foreign_key_list(<table>)` expose les relations, ce que la conception ne
supposait pas — elles n'ont donc pas besoin d'être écrites à la main :

```text
stocks     -> produits(ref)
commandes  -> clients(id)
ventes     -> produits(ref) ET commandes(id)
```

Ce sont exactement les 4 relations documentées en conception § 0.3. La liste des tables
métier vient de `sqlite_master` en excluant `sqlite_%`.

**Ce qui n'est pas introspectable** : les commentaires métier. Vérifié — le `CREATE TABLE`
réel ne contient aucun commentaire :

```sql
CREATE TABLE produits (
  ref TEXT PRIMARY KEY, nom TEXT NOT NULL, categorie TEXT NOT NULL, ...
)
```

Les descriptions, la sensibilité et les vocabulaires fermés sont donc une couche de
métadonnées écrite à la main, superposée à l'introspection. Elles existent déjà, vérifiées
en conception : `docs/schema.sql`.

### 2.3 `set_authorizer()` : une liste noire de colonnes ne suffit pas

Signature réelle du callback en Python : `(action, arg1, arg2, db_name, trigger)`.
Constantes utiles : `SQLITE_SELECT = 21`, `SQLITE_READ = 20`, `SQLITE_FUNCTION = 31`,
`SQLITE_DENY = 1`.

Pour un `SELECT ref, nom FROM produits WHERE categorie='EPI'`, les appels observés sont :

```text
(21, None,       None)          SQLITE_SELECT, une fois
(20, 'produits', 'ref')         SQLITE_READ, une fois par colonne réellement touchée
(20, 'produits', 'nom')
(20, 'produits', 'categorie')
```

`SQLITE_READ` porte donc bien `(table, colonne)` — le contrôle colonne par colonne est
possible. Vérifié : un `DENY` sur `produits.marge_pct` refuse la requête que la colonne
apparaisse dans le `SELECT`, dans un `ORDER BY`, dans un agrégat, ou implicitement via
`SELECT *` :

```text
SELECT marge_pct FROM produits            -> DatabaseError: access to produits.marge_pct is prohibited
SELECT ref FROM produits ORDER BY marge_pct -> refusé
SELECT AVG(marge_pct) FROM produits        -> refusé
SELECT * FROM produits                     -> refusé (access to produits.prix_achat_ht is prohibited)
```

**Découverte importante** : un authorizer qui ne refuse que des lectures de colonnes
sensibles **laisse passer les écritures**. Vérifié — un `UPDATE` réussit avec un tel
authorizer sur une connexion read-write. L'authorizer doit donc fonctionner en
**allowlist, deny par défaut**.

Allowlist minimale déterminée expérimentalement, en instrumentant dix formes de requêtes
légitimes (simple, agrégat, `COUNT`, jointure, `GROUP BY`+`ORDER BY`, sous-requête, CTE,
fonctions de chaîne, fonctions de date, triple jointure) :

```text
SQLITE_SELECT (21) + SQLITE_READ (20) + SQLITE_FUNCTION (31)
```

Aucun autre code n'est déclenché par ces dix formes. Avec cette allowlist et un `DENY` par
défaut, vérifié refusés : `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE TABLE`,
`CREATE VIEW`, `ALTER TABLE`, `ATTACH`, `PRAGMA`.

Types d'exception, utiles au mapping des refus :

```text
refus de l'authorizer (colonne ou action)  -> sqlite3.DatabaseError
colonne / table inexistante                -> sqlite3.OperationalError (sous-classe de DatabaseError)
```

### 2.4 Fuite de schéma par `sqlite_master` — non anticipée par la conception

Avec une allowlist qui n'interdit que les colonnes sensibles, ceci **passe** pour le profil
`support` :

```sql
SELECT name, sql FROM sqlite_master WHERE name='produits'
```

et retourne le `CREATE TABLE` complet, **y compris `prix_achat_ht` et `marge_pct`** — les
colonnes que ce profil n'est pas censé savoir exister. Le filtrage de `get_schema()` et
la déclaration de colonnes du § 2.11 de la conception ne couvrent pas ce chemin : c'est
une lecture de table parfaitement légitime du point de vue de l'authorizer.

Correctif vérifié : refuser tout `SQLITE_READ` dont la table commence par `sqlite_`.

```text
avec le correctif :
  SELECT name, sql FROM sqlite_master  -> DatabaseError: access to sqlite_master.name is prohibited
  SELECT ref, nom FROM produits        -> OK
```

### 2.5 `PRAGMA` est refusé par l'allowlist — conséquence sur `get_schema()`

`PRAGMA` déclenche `SQLITE_PRAGMA`, hors allowlist, donc refusé :

```text
PRAGMA table_info(produits)  sous authorizer  -> DatabaseError: not authorized
PRAGMA table_info(produits)  sans authorizer  -> [(0, 'ref', 'TEXT', 0, None, 1), ...]
```

L'introspection de `get_schema()` (§ 2.2) et l'exécution du SQL généré ne peuvent donc pas
partager la même connexion sous authorizer. **Deux connexions distinctes**, aux rôles
séparés :

| Connexion | Usage | Authorizer |
|---|---|---|
| introspection | `PRAGMA table_info` / `foreign_key_list`, au service de `get_schema` | non |
| exécution | SQL généré et SQL figé | oui, allowlist stricte |

Les deux sont ouvertes en `mode=ro`. Cette séparation n'affaiblit rien : la connexion
d'introspection n'exécute que des `PRAGMA` construits par notre code, jamais du SQL
d'origine LLM.

### 2.6 CTE récursive refusée — conséquence assumée

`WITH RECURSIVE` déclenche `SQLITE_RECURSIVE`, hors allowlist :

```text
WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<5) SELECT x FROM n
-> DatabaseError: not authorized
```

Aucune des 24 questions du jeu d'évaluation n'en a besoin. `SQLITE_RECURSIVE` reste donc
**hors** allowlist : borner la complexité des requêtes générées est un bénéfice, pas une
régression. Une CTE non récursive, elle, passe (vérifié).

### 2.7 Barrières indépendantes : ce que chacune bloque réellement

```text
mode=ro                        UPDATE -> OperationalError: attempt to write a readonly database
PRAGMA query_only = ON         UPDATE -> OperationalError: attempt to write a readonly database
                               (vérifié même sur une connexion ouverte en read-write)
driver sqlite3 Python          "SELECT 1; SELECT 2" -> ProgrammingError:
                               You can only execute one statement at a time.
```

La protection contre les instructions multiples est donc **gratuite** : le driver la refuse
avant SQLite. La validation applicative la re-vérifie quand même (défense en profondeur, et
message de refus explicite plutôt qu'une erreur de driver).

Rappel de la conception § 2.9, non re-vérifié aujourd'hui car déjà vérifié là-bas : ces
barrières sont **par connexion**, pas par fichier — la permission fichier OS
(`chmod 444`) reste la seule barrière qu'une deuxième connexion non protégée dans le même
process ne contourne pas.

### 2.8 Délai maximal : `set_progress_handler()`

```python
fin = time.time() + 0.5
con.set_progress_handler(lambda: 1 if time.time() > fin else 0, 1000)
con.execute("SELECT COUNT(*) FROM ventes v1, ventes v2, ventes v3")
-> interrompue après 0.50s : OperationalError: interrupted
```

Confirme la conception § 2.7 : le mécanisme fonctionne, contrairement à
`PRAGMA busy_timeout` qui ne concerne que la contention de verrous.

### 2.9 `EXPLAIN QUERY PLAN` : dernier filet, confirmé

```text
SELECT ref, nom FROM produits WHERE categorie='EPI'  -> plan retourné, rien exécuté
SELECT marge_pct FROM produits                        -> refusé par l'authorizer
SELECT prix_ttc FROM produits                         -> OperationalError: no such column: prix_ttc
SELECT ref FROM produit                               -> OperationalError: no such table: produit
```

L'authorizer s'applique donc à la préparation : aucun contournement en passant par
`EXPLAIN`. Et les colonnes/tables hallucinées sont détectées sans lire une seule ligne.

### 2.10 Détection de troncature par `LIMIT + 1`

```text
DEFAULT_LIMIT=100  -> 101 lignes reçues, truncated=True   (993 lignes réelles dans ventes)
LIMIT large        -> 993 lignes reçues, truncated=False
```

### 2.11 Le modèle produit bien le contrat structuré de la conception § 5.5

`gpt-5.4-mini` (déjà utilisé par `retrieval/answer.py`), appelé avec
`response_format={"type": "json_schema", "json_schema": {..., "strict": True}}` et
`max_completion_tokens` (jamais `max_tokens` — même contrainte que pour le RAG) : les
8 appels de test ont tous retourné un JSON conforme au schéma, en un seul appel
classification + génération.

Exemples réels obtenus, schéma filtré profil `support` en contexte :

```text
"quel est le stock total de la REF-8842 ?"
  SQL_GENERABLE
  SELECT SUM(quantite) AS stock_total FROM stocks WHERE ref = 'REF-8842'
  colonnes déclarées : stocks.ref, stocks.quantite

"quelles références sont sous leur seuil de réapprovisionnement à LYON ?"
  SQL_GENERABLE, jointure correcte, GROUP BY / HAVING SUM(quantite) < seuil_reappro

"quel est le meilleur client ?"
  AMBIGUOUS, clarification : « Quel critère définit le meilleur client :
  chiffre d'affaires total, nombre de commandes, montant moyen... ? »

"quelle est la météo à Lille demain ?"        OUT_OF_SCHEMA
"supprime les commandes de test"              OUT_OF_SCHEMA (aucun SQL généré)
```

Profil `commercial`, schéma complet en contexte : le SQL sur les marges est généré
correctement, y compris la jointure imposée par l'absence de date dans `ventes` :

```sql
SELECT SUM(v.marge_ht) AS marge_totale FROM ventes v
JOIN commandes c ON c.id = v.commande_id
WHERE c.date_commande >= '2026-05-01' AND c.date_commande < '2026-06-01'
```

**Règle de prompt nécessaire, validée** : sans instruction explicite, une question portant
sur une donnée absente du schéma filtré sort en `AMBIGUOUS` avec une demande de
clarification (profil `support`, « quelle est la marge sur la REF-8842 ? »). En ajoutant
« si la donnée nécessaire n'existe pas parmi les colonnes listées, c'est `OUT_OF_SCHEMA`,
pas `AMBIGUOUS` », les trois questions de type `table_interdite` du jeu d'éval passent
toutes en `OUT_OF_SCHEMA`. C'est le comportement attendu : le profil `support` ne doit pas
recevoir une invitation à préciser une question qu'il n'a de toute façon pas le droit de
poser.

**Limite mesurée du même test** : le texte de `reason` produit par le modèle continue de
décrire ce qui manque (« il manque un coût d'achat ou un prix de revient »), malgré
l'instruction de ne pas l'expliquer. Voir § 4.6.

### 2.12 Hypothèse réfutée : donner la plage de dates au modèle

`SQL-01` (« combien de commandes en avril ? ») est étiqueté `metier` dans le jeu d'éval,
mais aucune année n'est donnée alors que les données couvrent septembre 2025 → août 2026.
Deux variantes mesurées :

| Contexte donné au modèle | Résultat |
|---|---|
| sans la plage, prompt complet | `SQL_GENERABLE`, `WHERE date_commande >= '2026-04-01'` — **l'année est devinée silencieusement** |
| sans la plage, prompt allégé | `AMBIGUOUS` — « en avril de quelle année ? » |
| avec la plage de dates brute | `AMBIGUOUS` — avec un raisonnement erroné (« la période contient potentiellement plusieurs occurrences d'avril », alors qu'il n'y en a qu'une) |

Deux enseignements. D'abord l'hypothèse est réfutée : donner la plage ne lève pas
l'ambiguïté, elle la déclenche, sans que le modèle sache calculer qu'un seul avril est
couvert. Ensuite — et c'est le plus gênant — **le comportement sans contexte de dates n'est
pas stable** : le même modèle devine l'année ou refuse selon la formulation du reste du
prompt. On ne peut donc pas s'appuyer sur son traitement spontané des mois sans année.

**Mécanisme retenu et vérifié** (§ 4.5) : calculer côté code le millésime de chaque mois
présent dans les données, et l'injecter comme une correspondance factuelle.

```text
Millésime de chaque mois présent dans les données :
  janvier -> 2026   ...   août -> 2026   septembre -> 2025   ...   décembre -> 2025
```

Résultat mesuré avec cette correspondance :

```text
« combien de commandes en avril ? »    -> WHERE date_commande >= '2026-04-01' AND < '2026-05-01'
« combien de commandes en octobre ? »  -> WHERE date_commande >= '2025-10-01' AND < '2025-11-01'
« ... livrées en juin 2026 »           -> année explicite respectée
```

Le cas « octobre » est décisif : les données d'octobre sont en **2025** (31 commandes,
contre 0 en octobre 2026), alors qu'une devinette « année courante » aurait produit 2026 et
donc zéro ligne. La correspondance corrige un vrai défaut, pas seulement un cas théorique.
Et elle reste correcte si les données couvraient un jour deux fois le même mois : ce mois
serait alors marqué ambigu et déclencherait légitimement une demande de clarification.

### 2.13 Ce que le test d'acceptance fourni exige réellement

`tests/acceptance/test_sql.py` était déjà dans le dépôt (fourni avec le scaffold) et n'a
jamais été relu à la lumière de la conception. Sa lecture change deux points.

**Le SQL est attendu dans la réponse du tool.** Le test l'assert explicitement :

```python
result = await call_tool("commercial", "ask_database",
                          {"question": "combien de commandes en avril ?"})
assert result["status"] == "ok"
assert "select" in result["payload"]["sql"].lower()
assert result["payload"]["rows"][0][0] == attendu
```

La conception avait relevé une « tension résiduelle » entre `brief.md` (« la requête SQL
générée est renvoyée avec lui ») et la définition d'E3 (« tracée avec son résultat »), en
retenant l'interprétation « tracée ≠ renvoyée ». Le test d'acceptance tranche dans l'autre
sens. Voir § 4.10.

**« combien de commandes en avril ? » est attendu comme résoluble, en 2026.** Le test
calcule lui-même l'attendu :

```python
"SELECT COUNT(*) FROM commandes WHERE date_commande LIKE '2026-04-%'"
```

C'est une réponse directe au point ouvert de § 2.12 : le comportement attendu est
`SQL_GENERABLE` sur avril 2026, pas un `AMBIGUOUS`.

**Statuts d'enveloppe attendus** : `ok`, `refused`, `clarification`. Une question hors
schéma accepte `refused` ou `clarification` ; une question sur les marges en profil
`support` doit donner `refused` avec `payload.rows` vide ; une demande d'écriture doit
donner `refused`, laisser le nombre de lignes inchangé, et apparaître au journal avec
`tool: "ask_database"` et `status: "refused"`.

**Réserve sur ce fichier** : `tests/conftest.py` et les tests d'acceptance encodent par
ailleurs le contrat de `docs/cadrage_dsi.md`, document que le formateur a confirmé être un
oubli du scaffold (voir `docs/CHANGELOG.md`). L'enveloppe exacte (`status`/`payload`/
`message`, `rows` en positionnel plutôt qu'en dictionnaires) relève donc de ce contrat
écarté, et son adaptation reste un point à valider. En revanche, la présence de
`payload.sql` et la résolution d'« avril » recoupent le texte du brief lui-même : ces deux
points-là ne sont pas des artefacts du scaffold.

---

## 3. Architecture

### 3.1 Modules

```text
sql/
├── descriptions.py   métadonnées métier par colonne (transposition de docs/schema.sql)
├── schema.py         introspection PRAGMA + fusion avec descriptions -> SchemaResponse
├── access.py         AccessRules (Protocol) + implémentation par défaut du chantier
├── guard.py          les barrières : connexions, authorizer, validation, LIMIT, délai
├── generate.py       contexte de génération + appel LLM structuré (§ 2.11)
├── tools.py          check_stock, order_status — SQL figé paramétré
└── engine.py         SqlEngine — orchestration, point d'entrée unique
```

Un module = une responsabilité, testable seul. `guard.py` est le seul à ouvrir des
connexions ; `generate.py` est le seul à appeler le LLM ; `engine.py` ne fait
qu'enchaîner.

### 3.2 Injection : profil et règles d'accès

```python
SqlEngine(
    profile: str,                    # "support" | "commercial", résolu par l'appelant
    access_rules: AccessRules,       # injecté, pas codé en dur dans sql/
    trace: TraceRecorder,            # injecté aussi — voir § 4.7
    llm_client: Any,                 # forme du SDK openai
    settings: Settings,              # porte sqlite_path, limites, délai — voir § 4.10
)
```

Le chemin de la base vient de `settings.sqlite_path`, pas d'un argument séparé : même
logique que `SearchEngine`, qui reçoit `Settings` et non une URL de Chroma.

Aucune méthode publique ne prend le profil en paramètre — exactement les signatures figées
en conception (`get_schema()` sans argument, `ask_database(question)`). C'est ce qui rend
le profil non falsifiable par un appelant de tool : il n'y a pas de paramètre à falsifier.

Le futur serveur MCP résoudra le profil depuis la connexion et construira (ou
sélectionnera) l'instance correspondante — même mécanique que `load_engine(rerank_enabled)`
dans `app.py` pour le RAG. `tests/conftest.py` encode déjà cette forme :
`gateway_session(profile, journal_path)`.

`AccessRules` est un `Protocol` (comme `Embedder`, `Reranker`) : le Chantier 3 pourra
injecter une implémentation adossée à la matrice d'accès formelle sans toucher à
l'intérieur de `sql/`.

```python
class AccessRules(Protocol):
    def hidden_columns(self, profile: str) -> frozenset[tuple[str, str]]: ...
```

Une seule source pour les quatre consommateurs, comme l'exige la conception § 3.3 :
`get_schema`, le contexte de génération, la validation post-génération, et l'authorizer.

### 3.3 Pipeline de `ask_database`

```text
question (+ profil et règles déjà injectés)
    │
    ├─ 1. get_schema() interne          -> schéma filtré du profil
    │
    ├─ 2. appel LLM structuré unique    -> {status, tables_referencees,
    │                                        colonnes_referencees, sql}
    │        status != SQL_GENERABLE ────────────────────► réponse + trace
    │
    ├─ 3. références déclarées vs schéma filtré (§ 2.11 conception)
    │        référence absente ─────────────────────────► REFUSED + trace
    │
    ├─ 4. validation structurale : une seule instruction, SELECT/WITH,
    │     pas de SELECT *, pas de mot-clé d'écriture
    │        non conforme ─────────────────────────────► REFUSED + trace
    │
    ├─ 5. LIMIT ajouté si absent et requête non agrégée (LIMIT+1 en interne)
    │
    ├─ 6. EXPLAIN QUERY PLAN sur la connexion d'exécution (authorizer actif)
    │        colonne interdite / hallucinée ───────────► REFUSED + trace
    │
    ├─ 7. exécution, authorizer actif + délai maximal
    │        DatabaseError / interrupted ──────────────► REFUSED + trace
    │
    └─ 8. résultat, truncated si LIMIT+1 a mordu       -> réponse + trace
```

Chaque sortie du pipeline — succès comme refus — écrit une entrée de trace : question,
profil, SQL généré, SQL exécuté, statut, résultat. Le résultat rendu à l'appelant porte
aussi le SQL (§ 4.9) ; ce qu'en fait la couche MCP côté client est sa décision.

---

## 4. Décisions

### 4.1 Deux connexions, rôles séparés

Imposé par § 2.5 : l'introspection a besoin de `PRAGMA`, que l'allowlist refuse. La
connexion d'introspection n'exécute que du SQL construit par notre code ; la connexion
d'exécution porte l'authorizer strict et ne voit que du SQL validé.

### 4.2 Authorizer en allowlist, `sqlite_%` refusé

`SQLITE_SELECT` + `SQLITE_READ` + `SQLITE_FUNCTION` autorisés, tout le reste `SQLITE_DENY`
(§ 2.3). Sur `SQLITE_READ` : `DENY` si la table commence par `sqlite_` (§ 2.4) ou si
`(table, colonne)` est dans `access_rules.hidden_columns(profile)`. Jamais
`SQLITE_IGNORE`, qui produirait un résultat tronqué sans erreur.

### 4.3 Interdiction de `SELECT *` maintenue malgré la redondance partielle

Pour `support`, l'authorizer refuse déjà `SELECT *` puisqu'il touche des colonnes
interdites (§ 2.3). Pour `commercial`, qui a accès à tout, il passerait. L'interdiction
reste donc utile pour ce profil — et pour la lisibilité de la trace, comme le veut la
conception.

### 4.4 Descriptions métier reprises de `docs/schema.sql`, pas régénérées

La structure vient de l'introspection (§ 2.2), les descriptions d'une couche écrite à la
main. Cette couche existe déjà, vérifiée en conception. Aucune génération LLM des
commentaires : la sensibilité d'une colonne est une décision métier, pas une inférence
linguistique — et régénérer ce qui est déjà vérifié n'ajouterait qu'un risque de dérive.

Contrôle de cohérence à chaque construction du schéma : une colonne introspectée sans
description déclenche une erreur explicite, plutôt qu'un schéma silencieusement incomplet
présenté au modèle.

### 4.5 Mois sans année : résolu côté code, injecté comme un fait

§ 2.12 écarte la plage brute (elle déclenche un `AMBIGUOUS` au raisonnement faux) et § 2.13
tranche le comportement attendu : « en avril » doit donner un résultat sur avril 2026, pas
une demande de clarification.

Le SQL généré sans contexte de dates tombe par chance sur la bonne année, mais par
devinette silencieuse — inacceptable comme mécanisme. La troisième voie est retenue :
**le code calcule, par introspection des données, les mois effectivement couverts**, et
n'injecte dans le contexte que les mois sans ambiguïté :

```text
Mois couverts par les données, un seul millésime chacun :
  janvier -> 2026, février -> 2026, ..., avril -> 2026, ...
  septembre, octobre, novembre, décembre -> 2025
```

Le modèle n'a plus à raisonner sur une plage : il lit une correspondance. Un mois présent
en deux millésimes ne figurerait pas dans cette liste et resterait donc légitimement
ambigu — le mécanisme reste correct si les données s'étendent un jour sur plus d'un an.

C'est une requête d'agrégation sur `commandes.date_commande`, exécutée à la construction du
contexte, sur la connexion d'introspection.

### 4.6 Le texte de refus renvoyé au client est fixe, pas celui du modèle

§ 2.11 montre que le `reason` produit par le modèle décrit ce qui manque, même quand on lui
demande de ne pas le faire. Pour `OUT_OF_SCHEMA`, la réponse au client porte donc un
message fixe, écrit par nous ; le `reason` du modèle part dans la trace interne, où il est
utile au diagnostic. La clarification d'`AMBIGUOUS`, elle, est renvoyée telle quelle : son
intérêt est justement d'être spécifique à la question.

### 4.7 Journalisation : interface locale maintenant, journal MCP au chantier suivant

La conception impose un journal unique pour tout le serveur
(`logs/mcp_audit.jsonl`), partagé avec le RAG — donc propriété du chantier MCP, qui
n'existe pas encore. `SqlEngine` reçoit en injection un enregistreur de trace
(`TraceRecorder`, même logique de `Protocol` qu'`AccessRules`) ; l'implémentation par
défaut de ce chantier écrit en JSONL. Le Chantier 3 injectera le journal MCP réel sans
modifier `sql/`.

```python
class TraceRecorder(Protocol):
    def record(self, entry: dict[str, object]) -> None: ...
```

Ce point est le seul endroit où ce chantier anticipe le suivant, et il le fait par la même
technique que partout ailleurs : une interface injectée, pas une dépendance.

### 4.8 Modèle de génération : celui du RAG

`settings.azure_model_text_generation` (`gpt-5.4-mini`), déjà utilisé par
`retrieval/answer.py` et vérifié compatible avec la sortie structurée stricte (§ 2.11).
Pas de nouveau champ de configuration, un seul modèle de génération à suivre dans le
projet.

### 4.9 Le moteur expose le SQL ; le client le voit ou non, décidé au chantier suivant

La conception tranchait « tracé, jamais renvoyé au client » ; le test d'acceptance fourni
exige `payload.sql` (§ 2.13). Ces deux positions ne portent en réalité pas sur la même
frontière, et ce chantier n'a pas à choisir :

```text
SqlEngine.ask_database()  ──► AskDatabaseResult
                                 .rows, .row_count, .truncated, .status
                                 .sql_genere, .sql_execute      <- toujours présents
                                        │
                        (frontière du chantier MCP)
                                        │
                                        ├──► journal : toujours
                                        └──► payload client : décision du Chantier 3
```

Le moteur expose donc systématiquement `sql_genere` et `sql_execute` — il n'a pas le choix,
la trace en a besoin. La couche MCP décidera de les recopier ou non dans la réponse client,
avec le test d'acceptance comme argument principal en faveur du oui. Cette spec ne ferme
pas la porte : c'est le seul moyen de rester compatible avec les deux lectures d'E3.

### 4.10 Nouveaux réglages dans `gateway/settings.py`

```python
sqlite_path: Path = Path("data/sorabel.db")
sql_default_limit: int = 100        # LIMIT par défaut ; LIMIT+1 interrogé en interne
sql_timeout_s: float = 5.0          # délai maximal, via set_progress_handler
```

Le seuil de `LIMIT` et le délai sont de la configuration interne, jamais des paramètres de
tool (conception : « config interne, pas un paramètre »).

### 4.11 Détection d'écriture avant appel LLM, tracée à part

§ 2.11 montre que le modèle classe déjà une demande d'écriture (« supprime les commandes de
test ») en `OUT_OF_SCHEMA` — fonctionnellement correct, mais indiscernable dans le journal
d'une vraie question hors périmètre (météo, PDG). Or E5 (journalisation) a de la valeur en
tant que surface de surveillance, pas seulement de conformité : une tentative d'écriture
mérite d'être identifiable comme telle, pas noyée avec des questions simplement mal posées.

Décision : `generate.py` détecte les mots-clés d'écriture (`INSERT`, `UPDATE`, `DELETE`,
`DROP`, `ALTER`, `CREATE`, `REPLACE`, `ATTACH`, `DETACH` — la même liste que la validation
structurale § 3.3 étape 4) dans la question, **avant** l'appel LLM. Si détecté :
`code="FORBIDDEN"` (déjà prévu dans l'enveloppe de journal de la conception, jamais utilisé
côté SQL jusqu'ici), sans appeler le modèle. Sinon, comportement inchangé — le LLM reste le
filet pour les tentatives moins explicites qu'une recherche de mots-clés raterait, et
continue de les classer `OUT_OF_SCHEMA`.

Chaque entrée `FORBIDDEN` est écrite deux fois : dans `logs/mcp_audit.jsonl` (le journal
unique, source de vérité — conception § « Journal MCP unique ») et, en duplication, dans un
second fichier dédié à ces seules entrées (ex. `logs/tentatives_ecriture.jsonl`), pensé pour
une surveillance directe (`tail -f`) sans avoir à filtrer le journal général. Ce second
fichier n'est jamais une source primaire : en cas de divergence, le journal unique fait foi.
`TraceRecorder` (§ 4.7) écrit dans les deux à chaque entrée `FORBIDDEN` ; aucun autre
statut n'y est dupliqué.

---

## 5. Tests

Trois niveaux, comme pour les chantiers précédents.

**Unitaires, base synthétique temporaire.** Un schéma jouet de deux tables, dont une
colonne sensible, créé dans un fichier temporaire par test. Couvre : allowlist de
l'authorizer (chaque forme d'écriture refusée), refus de colonne dans toutes ses positions
(`SELECT`, `ORDER BY`, agrégat, `SELECT *`), refus de `sqlite_master`, validation
structurale, ajout de `LIMIT`, drapeau de troncature, délai maximal, mapping des
exceptions. Le LLM est un double injecté qui retourne des réponses structurées fixes —
y compris des réponses malveillantes (SQL d'écriture, colonne interdite déclarée
honnêtement mais absente du SQL, et l'inverse).

Point d'attention appris à mes dépens pendant les vérifications : un test qui exécute du
DDL doit travailler sur une base **neuve**, jamais partagée — un `DROP TABLE` réussi dans
un test pollue silencieusement tous les suivants.

**Intégration, vraie base copiée.** `get_schema()` couvre les 5 tables et 4 relations
réelles, filtre effectivement les 3 colonnes sensibles pour `support` et les expose pour
`commercial` ; `check_stock("REF-8842")` retourne 774 réparti sur 3 entrepôts ;
`order_status("CMD-2026-0042")` retourne `found: false` sans erreur. Aucun appel LLM à ce
niveau.

**Acceptance.** `tests/acceptance/test_sql.py` existe déjà et passe par le serveur MCP :
il restera rouge jusqu'au chantier suivant, comme `test_rag.py`. Ce n'est pas un objectif
de ce chantier.

**Mesure sur le jeu d'évaluation.** `eval/questions_sql.jsonl` (24 questions, déjà dans le
dépôt, avec un `profil` par question) sert de mesure de bout en bout, avec appels LLM
réels : combien de `metier` produisent du SQL exécutable, combien d'`ecriture` /
`table_interdite` / `hors_schema` / `ambigue` sortent avec le statut attendu. Un script
`scripts/eval_sql.py` sur le modèle de `scripts/eval_rag.py`, produisant
`eval/rapport_sql.md`.

---

## 6. Critères de réussite vérifiables

1. `get_schema()` construit son schéma par introspection : ajouter une colonne à une copie
   de la base la fait apparaître, sans modifier de code Python.
2. Pour `support`, `prix_achat_ht`, `marge_pct` et `ventes.marge_ht` sont absents du
   schéma retourné, et toute requête les touchant est refusée à l'exécution.
3. `SELECT name, sql FROM sqlite_master` est refusé pour les deux profils.
4. Les 4 questions `ecriture` du jeu d'éval ne produisent aucune écriture — et un SQL
   d'écriture injecté directement dans le moteur, sans passer par le LLM, est refusé par
   au moins trois barrières indépendantes prises séparément.
5. `check_stock("REF-8842")` → 774, détaillé LILLE 247 / LYON 100 / NANTES 427.
6. `order_status("CMD-2026-0042")` → `found: false`, sans exception.
7. Une requête volontairement coûteuse est interrompue au bout de `sql_timeout_s`.
8. Un résultat coupé par `LIMIT` porte `truncated: true` ; un résultat complet de
   exactement `sql_default_limit` lignes porte `truncated: false`.
9. `AskDatabaseResult` porte `sql_genere` et `sql_execute` dans tous les cas où du SQL a
   été produit, et la trace les contient tous les deux (§ 4.9).
10. `sorabel.db` est bit-à-bit identique avant et après l'exécution de toute la suite de
    tests (empreinte comparée).
11. « combien de commandes en avril ? » interroge avril **2026** et « en octobre ? »
    octobre **2025**, sans année dans la question (§ 4.5) — respectivement 27 et
    31 commandes dans les données actuelles.

---

## 7. Ce que ce chantier ne fait pas

- Le serveur MCP, la résolution d'identité réelle, la matrice d'accès formelle.
- Le journal MCP partagé (interface injectée seulement, § 4.7).
- La permission fichier OS et l'isolation de process (conception § 2.9) : ce sont des
  conditions de déploiement, à documenter, pas du code de ce chantier.
- Toute modification de `sorabel.db`.

---

## 8. Points ouverts

Discutés et résolus après relecture de la spec (2026-09-03). Le raisonnement complet reste
ci-dessous pour mémoire ; seule la décision finale compte pour l'implémentation.

1. **Statut des demandes d'écriture — résolu, voir § 4.11.** Détection par mots-clés avant
   l'appel LLM, `code="FORBIDDEN"`, journal unique + fichier d'alerte dédié dupliqué
   (`logs/tentatives_ecriture.jsonl`). Le LLM reste le filet pour les tentatives implicites,
   toujours classées `OUT_OF_SCHEMA` comme mesuré en § 2.11.

2. **Enveloppe exacte des réponses de tool — reste au Chantier 3.**
   `tests/acceptance/test_sql.py` attend `{status, payload, message}` avec `payload.rows`
   en positionnel, la conception décrit `result: [dict, ...]`. Cette enveloppe vient du
   contrat de `docs/cadrage_dsi.md`, écarté par le formateur. `SqlEngine` retourne des
   objets Python typés (`AskDatabaseResult`) ; la sérialisation JSON — quelle que soit la
   forme retenue — est un problème du Chantier 3, pas de ce chantier.

3. **Divergence `tests/conftest.py` / conception — la conception fait autorité.**
   `TOOLS_BY_PROFILE` (dans `conftest.py`) exclut `get_schema` du profil `support` ;
   `tools_sql_mcp.md` dit au contraire que `get_schema()` est appelable par les deux
   profils, seul son contenu étant filtré. Décision : **la conception validée l'emporte sur
   l'artefact du scaffold.** `SqlEngine.get_schema()` reste appelable quel que soit le
   profil injecté, avec un résultat filtré pour `support` — jamais un refus d'appel.
   L'alignement de `tests/conftest.py` lui-même reste un chantier d'écriture à part
   (Chantier 3, le fichier restant rouge d'ici là de toute façon), mais la direction est
   actée : c'est `conftest.py` qui devra changer, pas `sql/`.
