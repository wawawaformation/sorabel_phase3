# Spécification — Retrieval hybride, rerank et agent de démonstration

Chantier RAG avancé, deuxième et troisième étapes (brief, § Développement : « recherche
dense de base avec citations et refus hors corpus », puis « recherche hybride + reranking »).
Conception de référence : `conception/1_RAG/questions_reponses_rag.md`.
Étape précédente : `docs/spec_ingestion.md` (400 chunks indexés dans Chroma).

## 1. Objectif

Construire le retrieval hybride complet (Dense + BM25 + RRF + rerank Cohere) et un agent
en ligne de commande qui l'interroge, pour une démonstration du RAG hybride avec reranking.

**Dans le périmètre** : recherche dense sur Chroma, recherche lexicale BM25, fusion RRF,
filtrage des versions, diversification, rerank Cohere, décision de refus hors corpus (E1),
génération d'une réponse sourcée par `gpt-5.4-mini` côté client, agent CLI de démonstration,
mesure du gain hybride/rerank sur `eval/questions_rag.jsonl` (E6).

**Hors périmètre** : serveur MCP et protocole stdio (chantier suivant), tools SQL, matrice
d'accès appliquée aux collections (le RAG est ouvert aux deux profils — décision confirmée par
le formateur), interface graphique.

## 2. Faits vérifiés aujourd'hui

Tout ce qui suit est mesuré sur cette ressource Azure et ce corpus, pas supposé.

### 2.1 API de rerank Cohere sur Azure AI Foundry

Le SDK `openai` **ne peut pas** appeler le rerank : ce n'est pas une opération de l'API
OpenAI. La route se trouve dans la famille `/models` de la ressource Azure AI Services, et
non sous `/openai/v1`. Quatorze chemins ont été essayés avant de trouver le bon.

```text
POST https://<resource>.services.ai.azure.com/models/v1/rerank
     (?api-version=2024-05-01-preview — optionnel, la route répond aussi sans)
En-têtes : Content-Type: application/json
           api-key: <AZURE_AI_API_KEY>
Corps    : {"model": "Cohere-rerank-v4.0-pro",
            "query": "…",
            "documents": ["texte 1", "texte 2", …],
            "top_n": 10}
```

Réponse (format Cohere v1) :

```json
{"id": "…",
 "results": [{"index": 1, "relevance_score": 0.8527}, {"index": 0, "relevance_score": 0.1719}],
 "meta": {"api_version": {"version": "1"}, "billed_units": {"search_units": 1}}}
```

- `index` est la position dans le tableau `documents` envoyé (pas un identifiant métier).
- **Les résultats sont triés par `relevance_score` décroissant** (vérifié : le document
  pertinent en position 1 remonte avant celui en position 0).
- `relevance_score` est un score **absolu dans [0, 1]**, pas un rang.
- Facturation : 1 `search_unit` par appel, quel que soit le nombre de documents.

`Cohere-rerank-v4.0-fast` figure aussi au catalogue de la ressource, contrairement à ce qu'on
croyait — piste si la latence de `pro` posait problème.

### 2.2 Le score du reranker sépare le hors-corpus, le score de fusion non

Sur trois documents du corpus, mêmes documents pour les deux questions :

| Question | Meilleur score reranker |
|---|---|
| « que faire si un colis arrive endommagé ? » (couverte) | **0,853** |
| « quelle est la politique de télétravail chez Sorabel ? » (hors corpus) | **0,191** |

À l'inverse, BM25 seul **classe le hors-corpus plus haut qu'une question couverte** :
« politique de télétravail » obtient **7,36** sur la note « Point politique tarifaire » (match
lexical sur « politique »), contre **5,86** pour la meilleure réponse d'une question réellement
couverte. C'est la démonstration empirique de ce que la conception posait en principe
(§ « Ne pas utiliser directement le score RRF comme seuil de refus ») : **le seuil de refus se
fonde sur le score du reranker, jamais sur un score de recherche ou de fusion.**

### 2.3 Les quasi-doublons polluent réellement le top-k

Top 3 BM25 pour « que faire si un colis arrive endommagé ? » : le **même titre trois fois**
(`Procédure SAV — Colis reçu endommagé`), parce que le corpus contient à la fois plusieurs
versions du même document et plusieurs procédures voisines du même thème.

Conséquence : les **deux** mécanismes prévus à l'ingestion sont nécessaires, et pour des
raisons distinctes — ce qui tranche l'incohérence signalée dans `docs/spec_ingestion.md` § 9
(`TODO.md` parlait de `family_id`, la conception de `diversification_group`) :

- `family_id` → ne garder que la **dernière version** de chaque document logique ;
- `diversification_group` → ne garder qu'**un seul** représentant par thème métier.

### 2.4 Modèle de génération

`gpt-5.4-mini` répond via l'endpoint OpenAI-compatible (`/openai/v1`, SDK `openai`). Détail
d'API : il exige **`max_completion_tokens`**, le paramètre `max_tokens` est refusé.

### 2.5 Construction de l'index BM25

`collection.get(include=["documents", "metadatas"])` retourne bien les **400** documents avec
leurs métadonnées — l'index BM25 se reconstruit donc en mémoire au démarrage, sans rien
persister (conforme à `docs/spec_ingestion.md` § 9).

Tokenisation vérifiée : normalisation NFKD, retrait des diacritiques, minuscules, puis
`[a-z0-9]+`. Résultat sur `"Disjoncteur triphasé 63 A — REF-8842, 230/400 V"` :
`['disjoncteur', 'triphase', '63', 'a', 'ref', '8842', '230', '400', 'v']`. Les accents sont
correctement repliés (`triphasé` → `triphase`), les références éclatent en deux jetons
(`ref`, `8842`) — sans conséquence, les questions par référence exacte étant traitées par
routing et non par BM25 (§ 4.1).

### 2.6 Jeu d'évaluation

`eval/questions_rag.jsonl` : 30 questions — 8 `reference_exacte` (avec `attendu_reference`),
14 `couverte` (13 avec `attendu_type`, 1 avec `attendu_reference`), 8 `hors_corpus` (sans
attendu). C'est la cible de calibration du seuil de refus **et** la base de la mesure E6.

## 3. Pipeline retenu

```text
question
   │
   ├─ (0) routing : la question est-elle une référence exacte REF-nnnn ?
   │        └── oui → lookup métadonnée, pas de recherche (§ 4.1)
   │
   ▼ non
(1) Dense (Chroma, embedding Azure)  ──► classement A (30 candidats)
(2) BM25 (index mémoire, 400 docs)   ──► classement B (30 candidats)
   │
   ▼
(3) fusion RRF (k=60) ──────────────────► 20 candidats fusionnés
   │
   ▼
(4) filtrage des versions : 1 seul chunk par family_id (le plus récent)
   │
   ▼
(5) diversification : 1 seul chunk par diversification_group
   │
   ▼
(6) rerank Cohere sur les 10 premiers  ──► scores absolus [0, 1]
   │        (étape désactivable : RERANK_ENABLED)
   ▼
(7) décision de refus : meilleur score reranker < seuil ? ──► refus (E1)
   │
   ▼
(8) top_k final (5 par défaut) + citations
```

## 4. Décisions

### 4.1 Routing des références exactes hors du retrieval

Si la question contient une `REF-nnnn`, on ne cherche pas : on connaît la clé. Lookup direct
par métadonnée `ref_produit` dans Chroma, garantie déterministe (conception § « Mise à jour :
ce mécanisme a été remplacé par un routing côté client »). Couvre les 8 questions
`reference_exacte` du jeu d'éval sans dépendre du classement.

### 4.2 Constante RRF et volumes de candidats

RRF : `score(d) = Σ 1 / (k + rang(d))` avec **k = 60**, la valeur de référence de l'article
d'origine. La conception ne fixait ni k ni les volumes ; retenus ici :

| Étape | Volume |
|---|---|
| Candidats Dense | 30 |
| Candidats BM25 | 30 |
| Après fusion RRF | 20 |
| Envoyés au reranker | 10 |
| Retournés (`top_k` par défaut) | 5 |

Le reranker ne voit que 10 documents : il coûte 1 `search_unit` par appel quel que soit le
volume, mais sa latence croît avec le nombre de documents, et son rôle est d'affiner un petit
ensemble de bons candidats (conception § « Apport du reranking »), pas de trier tout le corpus.

### 4.3 Rerank désactivable, seuil de refus adossé au reranker

Le rerank est une étape **désactivable** par configuration (`RERANK_ENABLED`, déjà prévu en
conception et explicitement non exposé aux clients — `tools_rag_mcp.md`). Deux raisons :
mesurer son apport est une exigence (E6), et le noyau Dense+BM25+RRF doit rester fonctionnel
sans lui.

Conséquence à assumer et à documenter : **sans rerank, il n'y a pas de signal de refus
fiable** (§ 2.2 — BM25 et RRF classent un hors-corpus plus haut qu'une question couverte). Le
mode sans rerank retourne donc des résultats sans décision de refus ; il sert à la mesure E6
et au repli technique, pas à satisfaire E1.

Le seuil est un paramètre de configuration, **calibré** sur les 30 questions du jeu d'éval
(§ 5), pas choisi à la main. Valeur de départ pour la calibration : 0,40, au milieu de l'écart
mesuré (0,191 hors corpus / 0,853 couverte).

### 4.4 Appel HTTP direct pour le rerank

Le rerank passe par un appel HTTP explicite (`httpx`, déjà présent comme dépendance
transitive du SDK `openai`), pas par le SDK `openai` qui n'a pas cette opération. Le client de
rerank est **injecté** comme l'embedder de l'ingestion, pour que le pipeline reste testable
sans réseau.

### 4.5 Génération de la réponse côté client

La rédaction de la réponse est faite par `gpt-5.4-mini` **dans l'agent**, pas dans le
retrieval — conforme à la décision de conception « génération finale de texte : LLM côté
client, hors MCP ». Le retrieval retourne des passages et des citations ; l'agent compose.

Le prompt impose : répondre **uniquement** à partir des passages fournis, citer titre +
référence + date pour chaque affirmation, et dire explicitement qu'on ne sait pas si les
passages ne suffisent pas (E1).

### 4.6 Index BM25 en mémoire, construit au démarrage

Reconstruit depuis Chroma à l'initialisation du moteur de recherche (400 documents, coût
négligeable). Rien n'est persisté. Conséquence : un chunk ingéré après le démarrage de l'agent
n'est pas dans l'index BM25 tant qu'on ne redémarre pas — acceptable, l'ingestion étant un
traitement par lots.

## 5. Calibration du seuil et mesure E6

Un script d'évaluation exécute les 30 questions de `eval/questions_rag.jsonl` dans trois
configurations, et écrit un rapport dans `eval/rapport_gain.md` :

| Configuration | Composition |
|---|---|
| A — dense seul | Dense uniquement (référence « recherche simple ») |
| B — hybride | Dense + BM25 + RRF |
| C — hybride + rerank | Dense + BM25 + RRF + Cohere rerank |

Indicateurs, par type de question :

- `couverte` : le `attendu_type` (ou `attendu_reference`) apparaît-il dans le top-1 / top-5 ?
- `reference_exacte` : le routing retourne-t-il la bonne référence ? (attendu : 8/8, il est
  déterministe)
- `hors_corpus` : la question est-elle refusée ? (mesurable en configuration C uniquement)

Le seuil de refus est choisi comme la valeur qui refuse les 8 `hors_corpus` sans refuser
aucune des 14 `couverte`. Si aucune valeur ne satisfait les deux, le rapport documente le
compromis retenu plutôt que de le masquer.

## 6. Agent de démonstration

Un exécutable en ligne de commande, `scripts/demo_agent.py` :

```bash
uv run python scripts/demo_agent.py "que faire si un colis arrive endommagé ?"
uv run python scripts/demo_agent.py --no-rerank "…"   # montre l'apport du rerank
uv run python scripts/demo_agent.py --show-stages "…" # détaille chaque étape du pipeline
uv run python scripts/demo_agent.py                    # boucle interactive
```

Affichage attendu pour la démonstration :

1. l'étape de routing (référence exacte détectée ou non) ;
2. les candidats de chaque classement (Dense, BM25) puis après RRF, avec `--show-stages` ;
3. les scores du reranker, avant/après, pour montrer le réordonnancement ;
4. la décision de refus si le seuil n'est pas atteint ;
5. la réponse rédigée par `gpt-5.4-mini` avec ses citations (titre, référence, date).

`--no-rerank` et `--show-stages` existent pour la démonstration elle-même : montrer côte à côte
ce que change le reranking est exactement ce que demande E6.

## 7. Critères de réussite vérifiables

1. Les 8 questions `reference_exacte` retournent la bonne référence (routing déterministe).
2. Les 14 questions `couverte` ne sont pas refusées, et le `attendu_type` est dans le top-5.
3. Les 8 questions `hors_corpus` sont refusées en configuration C.
4. `eval/rapport_gain.md` existe et compare chiffres en main les configurations A, B et C.
5. Aucun doublon de `family_id` ni de `diversification_group` dans un top-k retourné.
6. Le pipeline complet tourne sans rerank (`RERANK_ENABLED=false`) sans erreur.
7. Tests unitaires : RRF sur classements connus, filtrage des versions, diversification,
   tokenisation, détection de référence — sans réseau (clients injectés).
8. Test d'intégration : pipeline complet contre Chroma éphémère, embedder et reranker factices.
9. L'agent produit une réponse sourcée sur une question couverte, et un refus explicite sur une
   question hors corpus.

## 8. Points ouverts

- **`answer_question` comme tool MCP** n'est pas construit ici : l'agent en fait l'équivalent
  côté client. L'assemblage en tool MCP relève du chantier suivant.
- **`list_sources` / `get_document`** ne sont pas construits comme tools : le routing des
  références exactes (§ 4.1) fait le lookup directement. À transformer en tools au chantier MCP.
- **Latence du rerank** non mesurée à ce stade ; `Cohere-rerank-v4.0-fast` est disponible en
  repli si `pro` s'avère trop lent pour un usage interactif.
- Le seuil calibré sur 30 questions reste un seuil calibré sur **30** questions : il vaut pour
  ce jeu d'éval, pas comme garantie générale.
