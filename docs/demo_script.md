# Script de démo — RAG hybride + rerank

Chantier RAG (ingestion + retrieval), démonstration en direct. Durée indicative : 12-15 min.

## Avant de commencer (hors chrono)

```bash
make up        # Chroma, si pas déjà démarré (docker compose)
make ui        # app Streamlit → http://localhost:8501
```

Vérifier que Chroma contient bien les 400 chunks avant d'ouvrir le navigateur :

```bash
uv run python -c "
from gateway.chroma import chroma_client, open_collection
from gateway.settings import get_settings
s = get_settings()
print(open_collection(chroma_client(s), s.chroma_collection).count())
"
```

Attendu : `400`. Si ce n'est pas le cas : `make ingest` (~13s).

---

## 1. Vue d'ensemble (1 min, à l'oral, sans écran technique)

« Sorabel a un corpus documentaire (fiches, notices, procédures SAV, notes internes — 400 documents) et une base SQL. Aujourd'hui je montre le chantier RAG : ingestion du corpus dans Chroma, puis un retrieval hybride — dense + lexical + fusion + reranking — avec un refus explicite quand la question sort du corpus. »

---

## 2. Onglet « 🔎 Recherche (answer_question) » — pipeline détaillé

1. Sidebar : activer **« Détailler le pipeline »**.
2. Poser : **« que faire si un colis arrive endommagé ? »**
3. Pointer à l'écran, dans l'ordre :
   - **Route : hybrid** — pas une référence exacte, donc le pipeline complet s'exécute
   - **1. Dense** — candidats trouvés par similarité vectorielle
   - **2. BM25** — candidats trouvés par matching lexical
   - **3. Fusion RRF** — les deux classements fusionnés (k=60)
   - **4-5. Dernière version / diversification** — dédoublonnage
   - **6. Rerank Cohere** — score absolu, ici autour de **0,87-0,88**
   - **Réponse** — texte rédigé par `gpt-5.4-mini`, citation exacte (titre — date)

Phrase clé : « Chaque étape est un module Python testé indépendamment — je peux le montrer dans le code si besoin (`retrieval/`). »

---

## 3. Comparaison avec / sans rerank — la preuve la plus parlante

Poser la **même question hors-corpus** dans les deux configurations.

**Avec rerank** (sidebar : rerank activé) :

> « quelle est la politique de télétravail chez Sorabel ? »

→ **❌ Refus — pertinence insuffisante : meilleur score 0.416 sous le seuil de 0.65**

**Sans rerank** (désactiver le toggle « Rerank activé », reposer la même question) :

→ Pas de refus. Des passages sont retournés quand même, sans score (`—`).

Discours : « Sans le rerank, il n'existe aucun signal absolu de pertinence — le score de fusion RRF classerait même cette question hors-corpus *au-dessus* d'une vraie question couverte (mesuré : 7,36 contre 5,86, un faux-positif lexical sur le mot "politique"). Le rerank n'est pas un confort, c'est la seule brique qui rend le refus possible. »

---

## 4. Onglet « 🧪 Recherche brute (search_docs) »

Réactiver le rerank. Poser : **« colis endommagé »**.

Montrer que sans dédup/diversification, les résultats bruts peuvent contenir plusieurs versions du même document — contrairement à l'onglet précédent qui les filtre.

*Optionnel si le temps le permet* — exemple RAG-19 (le dense rattrape ce que BM25 rate) :

```bash
uv run python scripts/demo_agent.py --show-stages --no-answer "quel différentiel choisir pour un circuit avec plaque de cuisson ?"
```

Pointer : `notice-REF-5521` apparaît dans **Dense** mais est absent des 30 candidats **BM25**.

---

## 5. Onglet « 📄 Récupérer un document (get_document) »

Choisir un exemple dans la liste (ex. `REF-8842-v2.1`). Montrer :
- métadonnées (collection, type, version)
- texte extrait
- les deux boutons de téléchargement : texte extrait **et** fichier source original (le vrai PDF, retrouvé sur le disque à partir de `collection` + `document_id` + `source`)

---

## 6. Onglet « 🗂️ Lister les sources (list_sources) »

Filtrer par référence : **`REF-8842`** → deux familles distinctes (la fiche technique **et** la notice partagent la même référence produit, mais sont deux documents logiques différents).

Cocher **« Inclure les versions antérieures »**, refiltrer sur la collection `sav` → une famille avec `v2.0` en courant et `v1.0` en version antérieure.

---

## 7. Les preuves chiffrées (si public technique / formateur)

```bash
cat eval/rapport_gain.md
```

Points à citer :
- **Références exactes : 8/8** dans les trois configurations (routing déterministe, ne dépend jamais du classement).
- **RRF seul (config B) ne bat pas clairement le dense seul (config A)** sur les 14 questions couvertes — honnête, pas caché : la vraie amélioration nette vient de l'ajout du rerank (config C).
- **Seuil de refus calibré à 0,65** (pas une valeur choisie à la main) : max hors-corpus mesuré 0,626, min couvertes mesuré 0,669 — séparation parfaite.

Relancer en direct si le temps permet (~35s, appels Azure réels) :

```bash
make eval
```

---

## 8. Le code (si public technique)

- `docs/schemas/ingest_fichiers.drawio` / `retrieval_fichiers.drawio` — enchaînement des fichiers, options CLI, principe d'injection de dépendances (pourquoi les tests tournent sans Docker ni réseau).
- `docs/schemas/rerank_reel.drawio` — l'écart conception/réel sur l'API de rerank (14 routes testées avant de trouver `POST .../models/v1/rerank`).
- `gateway/settings.py` — chaque variable commentée : rôle dans le pipeline, fichier qui la consomme.

---

## Questions probables et réponses courtes

| Question | Réponse |
|---|---|
| Pourquoi BM25 si sa contribution n'est pas prouvée ? | Coût quasi nul (en mémoire, pas d'appel API), filet de sécurité lexical théorique, alimente le rerank même si son apport propre n'est pas démontré sur ces 14 questions. |
| Pourquoi pas de journal/logs visibles ? | La journalisation (E5) est la responsabilité du serveur MCP (chantier suivant) — les scripts de démo appellent le moteur directement en Python, sans passer par une couche MCP à journaliser. |
| Le profil (support/commercial) sert à quoi dans l'UI ? | Rien pour l'instant — pas de matrice d'accès avant le chantier MCP. Assumé et dit explicitement, pas caché. |
| Pourquoi Streamlit et pas juste le CLI ? | Livrable « interface graphique » du brief — construit pour la démo RAG, pas encore le livrable complet (SQL, matrice d'accès à ajouter plus tard). |
