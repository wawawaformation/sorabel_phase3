# Guide d'accès — Sorabel Data Gateway

> Destiné aux équipes clientes internes (bot Slack du support, IDE des développeurs,
> poste des commerciaux) qui intègrent la gateway. Pour le détail de conception, voir
> `conception/commun/catalogue_tools_mcp.md` ; pour l'architecture de déploiement,
> `docs/spec_deploiement.md`.

## 1. Se connecter

Deux modes coexistent, selon le contexte :

| Mode | Transport | Profil résolu par | Usage |
|---|---|---|---|
| Démo locale | stdio (`python -m mcp_server.server`) | `SORABEL_PROFILE` (variable d'environnement du process) | `scripts/mcp_client.py`, `app_gateway.py`, tests |
| Déployé | HTTP (`http://<gateway>:8090/mcp`) | Jeton JWT Keycloak (`Authorization: Bearer <token>`) | `docker compose up`, intégration réelle |

Dans les deux cas, **le profil n'est jamais un paramètre de tool** — il est résolu côté
serveur (variable d'environnement du process en local, rôle réalm du jeton en HTTP).
Un appel qui tenterait de le fournir en argument serait ignoré : le champ n'existe dans
le schéma d'entrée d'aucun tool.

### Obtenir un jeton (mode déployé)

```bash
curl -s -X POST http://localhost:8180/realms/sorabel/protocol/openid-connect/token \
  -d "client_id=sorabel-gateway" -d "grant_type=password" \
  -d "username=commercial-demo" -d "password=demo"   # ou support-demo
```

## 2. Les deux profils

| | `support` | `commercial` |
|---|---|---|
| Tous les tools | ✅ | ✅ |
| Toutes les collections documentaires | ✅ | ✅ |
| Colonnes SQL sensibles (`produits.prix_achat_ht`, `produits.marge_pct`, `ventes.marge_ht`) | ❌ jamais retournées | ✅ |

Aucun tool n'est interdit dans son intégralité à un profil — la seule restriction porte
sur ces 3 colonnes SQL. `get_schema` reste appelable par les deux profils : son contenu
est simplement filtré pour `support`.

## 3. Catalogue des 8 tools — quand utiliser lequel

### RAG (documentaire)

| Tool | Utiliser pour | Ne pas utiliser pour |
|---|---|---|
| `answer_question(question, top_k=5)` | Question documentaire générale : réponse rédigée et sourcée | Explorer sans générer (→ `search_docs`) |
| `search_docs(query, top_k=5, include_score=false)` | Recherche brute, exploration/diagnostic | Réponse finale à un utilisateur |
| `list_sources(collection?, type_doc?, ref_produit?, include_versions=false)` | Résoudre une référence exacte `REF-xxxx` vers un `document_id` | — |
| `get_document(doc_id)` | Récupérer un document déjà identifié | Relancer une recherche approximative |

**Routing recommandé** : référence exacte connue → `list_sources` puis `get_document`.
Question générale → `answer_question` directement.

### SQL (données structurées)

| Tool | Utiliser pour | Ne pas utiliser pour |
|---|---|---|
| `check_stock(ref)` | Stock d'une référence produit précise (figé, sans LLM) | Question SQL variable (→ `ask_database`) |
| `order_status(order_id)` | Statut d'une commande identifiée (figé, sans LLM) | idem |
| `get_schema()` | Connaître le périmètre SQL accessible avant d'écrire une question | — |
| `ask_database(question)` | Question métier variable, quand aucun tool figé ne couvre le besoin | Stock ou statut de commande (tools dédiés plus fiables) |

`ask_database` ne renvoie **jamais** le SQL généré/exécuté dans sa réponse — seul le
journal (§ 5) le conserve.

## 4. Enveloppe de réponse

Chaque appel renvoie un texte JSON `{"status", "payload", "message"}` :

| `status` | Sens |
|---|---|
| `ok` | Résultat métier dans `payload` |
| `refused` | Refus contrôlé (colonne interdite, écriture détectée, structure invalide) |
| `hors_corpus` | RAG : aucune source suffisamment pertinente |
| `clarification` | SQL : question ambiguë, `message` porte la précision demandée |
| `error` | Ressource introuvable (ex. `get_document` sur un id inconnu) |

Le mode HTTP porte en plus le contrat MCP natif : `isError` (faux seulement si
`status == "ok"`) et `_meta["sorabel/error_code"]` parmi `FORBIDDEN`, `OUT_OF_CORPUS`,
`OUT_OF_SCHEMA`, `AMBIGUOUS` quand applicable.

## 5. Journal

Tout appel, autorisé ou refusé, est journalisé dans `logs/mcp_audit.jsonl` (un fichier
unique, RAG et SQL confondus) : `profil`, `tool`, `question`, `statut`, `code`,
`detail`. Consultable en direct : `make journal` (ou `tail -f logs/mcp_audit.jsonl`).
