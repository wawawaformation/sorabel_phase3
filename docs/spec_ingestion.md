# Spécification — Ingestion du corpus documentaire

Chantier RAG avancé, première étape (brief, § Développement). Conception de référence :
`conception/1_RAG/questions_reponses_rag.md`.

## 1. Objectif

Transformer les 400 documents hétérogènes de `data/corpus/` en chunks indexés dans Chroma,
prêts pour le retrieval, avec les métadonnées nécessaires aux citations (E1), à la recherche
par référence exacte (E2) et au filtrage par profil (E4).

**Dans le périmètre** : lecture des fichiers, extraction du texte, extraction des
métadonnées, construction du document canonique, chunking, calcul des embeddings,
écriture dans Chroma, ré-exécution idempotente.

**Hors périmètre** (étapes suivantes) : recherche dense, BM25, RRF, reranking, diversification,
décision de pertinence, tools MCP. Aucun LLM génératif n'intervient dans l'ingestion — seul un
modèle d'**embedding** est appelé.

## 2. Corpus réel — mesures vérifiées

Mesuré sur `data/corpus/` de ce dépôt (pas sur la transcription de travail utilisée en conception) :

| Collection | Fichiers | Format | Familles | Familles à 2 versions |
|---|---|---|---|---|
| `fiches` | 150 | PDF | 120 | 30 |
| `notices` | 80 | PDF | 70 | 10 |
| `sav` | 90 | HTML | 80 | 10 |
| `notes` | 80 | Markdown | 80 | 0 |
| **Total** | **400** | | **350** | **50** |

Régularité constatée sur l'intégralité du corpus (aucun échantillonnage) :

- **PDF (230 fichiers)** : tous mono-page, métadonnées PDF embarquées **vides** (`{}`) — tout
  s'extrait du texte. Les 4 champs requis (titre, référence, version, date) sont extractibles
  par regex sur **230/230** fichiers.
- **HTML (90 fichiers)** : une seule et même séquence de balises pour les 90 ; les 3 balises
  `<meta>` (`version`, `date`, `type`) présentes exactement 3 fois par fichier ;
  `type` toujours `procedure_sav` ; versions ∈ {`1.0`, `2.0`} ; dates toutes en `YYYY-MM-DD`.
- **Markdown (80 fichiers)** : front-matter YAML présent partout, exactement les mêmes 5 clés
  (`titre`, `date`, `auteur`, `type`, `version`) ; `type` toujours `note_interne` ; `version`
  toujours la chaîne **quotée** `'1.0'` (les guillemets sont à retirer) ; dates toutes en
  `YYYY-MM-DD` ; aucun titre vide ; corps de 11 lignes partout, **aucun** tableau, liste, lien
  ou bloc de code.
- **`document_id`** : les 400 noms de fichiers (sans extension) sont **uniques** sur l'ensemble
  du corpus — utilisables directement comme identifiants.

## 3. Décisions

### 3.1 `pypdf` plutôt que `pdftotext -layout` — écart assumé avec la conception

La conception retenait `pdftotext -layout`. Mesure faite ici sur les **230 PDF** : `pypdf` et
`pdftotext -layout` produisent des valeurs extraites **identiques** (référence, version, date),
avec **0 échec** de part et d'autre.

`pypdf` est retenu parce que, à résultat égal :

- c'est une dépendance Python déjà déclarée dans `pyproject.toml` (choix du scaffold) ;
- `pdftotext` est un binaire système (poppler-utils) — une hypothèse d'environnement
  supplémentaire à satisfaire en CI, en Docker et sur chaque poste.

L'écart est documenté ici plutôt que corrigé dans la conception : la conception reste la trace
de ce qui avait été décidé avec les données disponibles à l'époque.

### 3.2 Extraction Markdown : `markdown` + front-matter maison

Le corps est converti Markdown → HTML par `markdown`, puis réduit en texte brut par
`BeautifulSoup` — **le même extracteur que pour le HTML de `sav/`**, donc un seul chemin de code
d'extraction pour deux formats.

Le front-matter est lu par un parseur minimal (découpage sur les délimiteurs `---`, puis
`clé: valeur` avec retrait des espaces et des guillemets encadrants). `PyYAML` n'est pas ajouté :
les 5 clés sont invariantes et les valeurs sont des scalaires simples.

### 3.3 Une seule collection Chroma

La conception ne tranchait pas ce point. Décision : **une seule collection Chroma**
(`sorabel_corpus`), le champ `collection` (`fiches` | `notices` | `sav` | `notes`) vivant dans
les métadonnées et servant de filtre (`where`).

Raison : une question porte normalement sur plusieurs collections autorisées à la fois. Avec
quatre collections physiques il faudrait interroger quatre index puis fusionner les résultats à
la main, pour un seul bénéfice — une isolation physique dont on n'a pas besoin, le filtrage par
profil étant appliqué côté serveur de toute façon.

Contrainte constatée : un nom de collection Chroma doit faire 3 à 63 caractères
alphanumériques (plus `_` et `-`).

### 3.4 Embeddings toujours fournis explicitement à Chroma

Les vecteurs sont calculés par nous via Azure et passés à Chroma dans `embeddings=[...]`.
**Aucune fonction d'embedding n'est configurée sur la collection** : laisser Chroma calculer
lui-même déclencherait le téléchargement de son modèle ONNX par défaut — exactement la
dépendance lourde locale qu'on a écartée en passant sur Azure.

### 3.5 Identifiants déterministes et ré-exécution idempotente

- `document_id` = nom du fichier sans extension (unicité vérifiée sur les 400 fichiers).
- `chunk_id` = `<document_id>#<chunk_index>`.

L'écriture se fait par `upsert`. Vérifié : un `upsert` répété sur le même identifiant met à jour
sans créer de doublon (le `count()` reste stable). Une ré-exécution complète de l'ingestion est
donc sans effet de bord, sans avoir à vider la collection au préalable.

### 3.6 Point d'entrée : `scripts/ingest.py` + cible `make ingest`

Sur le modèle de l'existant (`scripts/seed.py` / `make seed`) : un script exécutable et une
cible Make, à ajouter au `Makefile` (elle n'existe pas encore).

### 3.7 Pas de découpeur implémenté

1 chunk = 1 document, `chunk_index` toujours `0` : mesuré sur ce corpus (tous les documents sont
très en-deçà d'une taille justifiant un découpage). Le découpage structurel décrit en conception
reste un **repli non implémenté** — écrire un découpeur que rien ne déclenche serait du code mort.
Il sera construit le jour où le corpus contiendra des documents assez longs pour l'exiger.

### 3.8 Noms de métadonnées internes

Les noms de champs stockés dans Chroma (`ref_produit`, `type_doc`…) sont **internes**. La suite
d'acceptance fournie attend d'autres noms côté tools (`metadata.reference`,
`metadata.doc_type`) : la correspondance se fait à la frontière MCP, pas dans l'index. Les
*valeurs*, elles, coïncident déjà (`fiche_technique`, `notice`, `procedure_sav`, `note_interne`).

## 4. Pipeline

```text
data/corpus/<collection>/<fichier>
        │
        ├── PDF  ──► pypdf ──────────────────────────┐
        ├── HTML ──► BeautifulSoup ──────────────────┤
        └── MD   ──► markdown → BeautifulSoup ───────┤
                     + front-matter maison           │
                                                     ▼
                                        texte brut + métadonnées
                                                     │
                                                     ▼
                                        DocumentCanonique (ingest/document.py)
                                                     │
                                                     ▼
                                        chunking (1 chunk = 1 document ici)
                                                     │
                                                     ▼
                                             Chunk (ingest/chunk.py)
                                                     │
                                     ┌───────────────┴───────────────┐
                                     ▼                               ▼
                    embedding Azure sur                    métadonnées scalaires
                    title + ref_produit + content                    │
                                     └───────────────┬───────────────┘
                                                     ▼
                                        Chroma.upsert (collection sorabel_corpus)
```

## 5. Règles d'extraction par format

### 5.1 PDF (`fiches/`, `notices/`)

Texte concaténé sur toutes les pages via `pypdf` (tous mono-page en pratique), puis regex :

| Champ | Motif |
|---|---|
| `title` | `^(?:FICHE TECHNIQUE\|NOTICE D'INSTALLATION)\s*-\s*(.+)$` (multiligne) |
| `ref_produit` | `R[ée]f[ée]rence produit\s*:\s*(REF-\d{4})` |
| `version` | `Version\s*:\s*([\d.]+)` |
| `date` | `Date\s*:\s*(\d{4}-\d{2}-\d{2})` |

Les regex portent sur le texte entier, sans hypothèse de position de ligne : la mise en page
diffère entre les deux collections (dans une fiche, `Version`/`Date` sont sur leur propre ligne ;
dans une notice, référence, version et date sont sur la même ligne).

`Accessoires et produits associés : REF-…, REF-…` (présent dans les fiches) est une **mention**,
pas l'identité du document : non extrait (voir § 9).

### 5.2 HTML (`sav/`)

- `title` : contenu de `<title>` (identique au `<h1>`).
- `version`, `date`, `type_doc` : attributs `content` des `<meta name="version|date|type">`.
- `content` : texte du `<body>`, balises retirées, espaces normalisés.
- `ref_produit` : **aucun**. Ces procédures sont génériques (« Applicable à tout le catalogue »),
  la référence citée n'est qu'un exemple.

### 5.3 Markdown (`notes/`)

- Front-matter : `titre`, `date`, `auteur`, `type`, `version` (guillemets encadrants retirés).
- `content` : corps après le front-matter, converti en HTML puis réduit en texte brut.
- `ref_produit` : **aucun** (même raison qu'en SAV — la référence du corps est une mention).

## 6. Dérivation des champs

| Champ | Règle |
|---|---|
| `document_id` | nom du fichier sans extension (`REF-1024-v2.1`, `note-2024-01-11-alerte-qualite-50`) |
| `collection` | nom du dossier (`fiches`, `notices`, `sav`, `notes`) |
| `type_doc` | déduit de la collection : `fiche_technique`, `notice`, `procedure_sav`, `note_interne` — jamais classifié |
| `family_id` | `document_id` privé du suffixe `-v<version>` (`REF-1024`, `notice-REF-1459`, `proc-casse-transport-01`) ; pour `notes`, le `document_id` lui-même (aucune version dans les noms) |
| `diversification_group` | `sav` : `sav_<thème>` (10 thèmes) ; `notes` : `note_<thème>` (5 thèmes) ; `fiches`/`notices` : égal au `family_id` |
| `version`, `date`, `title`, `ref_produit` | extraits (§ 5) |
| `source` | `pdf` \| `html` \| `md` |
| `chunk_index` | `0` sur ce corpus (1 chunk = 1 document) |

Le thème SAV s'obtient en retirant du nom le préfixe `proc-`, le numéro d'ordre et la version
(`proc-casse-transport-01-v2.0` → `casse-transport`). Le thème d'une note s'obtient en retirant
le préfixe `note-<date>-` et le numéro final (`note-2024-01-11-alerte-qualite-50` →
`alerte-qualite`).

`fiches` et `notices` ne sont volontairement pas regroupées au-delà de leur famille : deux
produits distincts ne sont pas des quasi-doublons, et les regrouper ferait disparaître des
résultats légitimes lors d'une comparaison de produits.

**Toutes les versions sont indexées**, y compris les antérieures : le filtrage « dernière version
par défaut » est une décision de *retrieval*, et `get_document` doit pouvoir retrouver n'importe
quelle version par son identifiant.

## 7. Écriture dans Chroma

Contraintes vérifiées empiriquement sur `chromadb` 0.5 :

- une valeur de métadonnée doit être `str`, `int`, `float` ou `bool` ;
- **`None` est refusé** (`ValueError`) → pour `sav` et `notes`, la clé `ref_produit` est
  **omise**, pas mise à `None` ;
- **une liste est refusée** → aucun champ multi-valué n'est stocké tel quel ;
- `date` est stockée en chaîne ISO `YYYY-MM-DD` (et non en objet `date`).

Appel : `upsert(ids=[chunk_id], embeddings=[vecteur], documents=[content], metadatas=[…])`.
`documents` reçoit le `content` **brut** — c'est lui qui sert aux citations et qui alimentera
BM25 ; seul le vecteur est calculé sur le texte enrichi.

## 8. Embeddings

- Endpoint : `AZURE_AI_ENDPOINT` (compatible OpenAI `/openai/v1`), clé `AZURE_AI_API_KEY`,
  modèle `AZURE_MODEL_TEXT_EMBEDDING_SMALL` (`text-embedding-3-small`).
- Client : SDK `openai` avec `base_url` pointé sur l'endpoint Azure.
- Texte embeddé : `title + ref_produit + content` (décision de conception § 2 —
  aide le matching sémantique quand la requête ne contient pas de `REF-xxxx` explicite).
  `ref_produit` est omis du préfixe quand il n'existe pas.
- Appels groupés (l'API accepte un tableau de textes) pour limiter le nombre de requêtes sur 400
  documents.
- La dimension du vecteur n'est pas fixée en dur : elle est celle que renvoie le modèle.

## 9. Hors périmètre et points ouverts

- **`refs_citees`** : les fiches listent des références associées et les procédures SAV/notes en
  citent en exemple. Ni `DocumentCanonique` ni `Chunk` ne portent ce champ, et E1/E2 n'en ont pas
  besoin (E2 cherche le document *dont* `ref_produit` correspond, pas ceux qui la mentionnent —
  conception § « Pourquoi `ref_produit` est décisive »). Non implémenté. À rouvrir seulement si un
  besoin « quels documents mentionnent REF-X » apparaît ; il faudrait alors une chaîne jointe,
  Chroma refusant les listes.
- **Politique de diversification au retrieval** : `TODO.md` parle d'« au plus 1 résultat par
  `family_id` », la conception de `diversification_group`. L'ingestion peuple correctement les
  deux champs ; l'incohérence est à trancher dans la spec de retrieval.
- **Index BM25** : `rank-bm25` travaille en mémoire. Il sera reconstruit au démarrage du
  retrieval à partir des `documents` stockés dans Chroma — rien à persister à l'ingestion.
- **Contrat d'intégration à trancher (hors ingestion)** : la suite `tests/acceptance/` fournie
  encode le contrat de `docs/cadrage_dsi.md` — document que le formateur a demandé de considérer
  comme inexistant, mais que les tests continuent de référencer (enveloppe
  `{status, payload, message}`, matrice où `support` n'a pas `get_schema`). Ce conflit ne touche
  pas l'ingestion, qui est interne, mais devra être tranché avant d'implémenter les tools MCP.

## 10. Gestion des erreurs

L'ingestion **échoue fort** (exception, code de sortie non nul) si un fichier ne livre pas ses
champs obligatoires (`title`, `version`, `date`) ou si un `document_id` s'avère dupliqué. Le
corpus est régulier sur les 400 fichiers : une extraction qui échoue signale une hypothèse
devenue fausse, pas un cas limite à contourner silencieusement. Le message d'erreur nomme le
fichier fautif et le champ manquant.

`ref_produit` absent n'est pas une erreur : c'est le cas normal de `sav` et `notes`.

## 11. Critères de réussite vérifiables

1. `make ingest` traite les 400 fichiers sans erreur et la collection Chroma contient
   **exactement 400** chunks.
2. Répartition par collection dans les métadonnées : 150 / 80 / 90 / 80.
3. `ref_produit` présent sur les 230 chunks issus des PDF, absent sur les 170 autres.
4. 350 `family_id` distincts, dont 50 familles portant 2 versions.
5. Une seconde exécution de `make ingest` laisse le compte à 400 (idempotence).
6. Tests unitaires : extraction correcte des champs sur un fichier de référence de chaque
   format ; dérivation de `family_id` et `diversification_group` sur les cas nommés au § 6.
7. Test d'intégration : ingestion complète contre un Chroma éphémère, puis vérification des
   comptages 1 à 4 ci-dessus.
