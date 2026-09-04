"""Configuration partagée par l'ingestion, le retrieval et (plus tard) le serveur MCP.

Un seul point d'entrée (``Settings``) pour toutes les variables d'environnement du
projet : accès à Chroma, credentials Azure AI Foundry, et les réglages numériques du
pipeline de retrieval hybride (volumes de candidats par étage, seuil de refus, k de
la fusion RRF). Les valeurs par défaut correspondent à l'état calibré au 2026-09-02
(voir eval/rapport_gain.md) — les modifier change le comportement du pipeline pour
tous les consommateurs (ingest/, retrieval/, app.py, scripts/) sans recompilation.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" : .env porte aussi des variables d'autres modules
    # (SORABEL_PROFILE, GATEWAY_JOURNAL…).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Corpus & index ---
    corpus_dir: Path = Path("data/corpus")  # source de l'ingestion (400 fichiers, 4 sous-dossiers)
    chroma_url: str = "http://localhost:8002"  # docker compose (make up)
    chroma_collection: str = "sorabel_corpus"  # collection unique, remplie par make ingest

    # --- Azure AI Foundry ---
    azure_ai_endpoint: str = ""  # base OpenAI-compatible (/openai/v1)
    azure_ai_api_key: str = ""
    azure_model_text_embedding_small: str = "text-embedding-3-small"  # embeddings (ingest+requête)
    azure_model_reranking: str = "Cohere-rerank-v4.0-pro"  # /models/v1/rerank, hors SDK openai
    azure_model_text_generation: str = "gpt-5.4-mini"  # rédaction réponse (retrieval/answer.py)

    # --- Retrieval : réglages du pipeline (retrieval/engine.py) ---
    rerank_enabled: bool = True  # si False : pas de score absolu, pas de refus (spec §4.3)
    # Calibré sur eval/questions_rag.jsonl (scripts/eval_rag.py, voir eval/rapport_gain.md) :
    # max(hors corpus)=0.626, min(couvertes)=0.669, séparation parfaite entre 0.64 et 0.66.
    # Score du reranker uniquement — jamais le score RRF (il classe un hors-corpus au-dessus
    # d'une question couverte, voir docs/schemas/rerank_reel.drawio).
    refusal_threshold: float = 0.65
    dense_candidates: int = 30  # candidats remontés par la recherche dense (Chroma)
    lexical_candidates: int = 30  # candidats remontés par BM25 (retrieval/lexical.py)
    fusion_candidates: int = 20  # candidats après fusion RRF, avant dédup/diversification
    rerank_candidates: int = 10  # candidats envoyés au reranker (1 appel = 1 search_unit facturé)
    top_k: int = 5  # résultats finalement retournés par search()/answer_question
    # constante RRF (article d'origine) : petit k -> le rang exact domine,
    # grand k -> la présence dans plusieurs classements domine (tests/unit/test_fusion.py)
    rrf_k: int = 60

    # --- Text-to-SQL : accès à la base et garde-fous (sql/) ---
    sqlite_path: Path = Path("data/sorabel.db")  # généré par make seed, jamais modifié
    sql_default_limit: int = 100  # LIMIT des requêtes de liste ; LIMIT+1 interrogé en interne
    sql_timeout_s: float = 5.0  # délai maximal d'exécution, via set_progress_handler
    # Duplication des seules tentatives d'écriture, pour surveillance directe (spec § 4.11).
    # Le journal MCP unique reste la source de vérité, ce fichier n'en est qu'une vue.
    sql_alert_log: Path = Path("logs/tentatives_ecriture.jsonl")

    # --- Observabilité LLM (Langfuse Cloud) : uniquement la génération SQL pour l'instant ---
    # Le wrapper langfuse.openai patche openai.OpenAI globalement au process (pas par
    # instance) : ne l'importer que dans un script qui ne construit pas d'autre client
    # OpenAI (embeddings RAG comprises) tant qu'un seul process ne mélange pas les deux.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"

    # --- Serveur MCP HTTP + IdP Keycloak (mcp_server/http_server.py) ---
    keycloak_issuer: str = "http://localhost:8180/realms/sorabel"
    keycloak_audience: str = "sorabel-gateway"  # = azp attendu (Keycloak n'émet pas "aud"
    # par défaut pour un client public sans mapper dédié — vérifié empiriquement)
    http_host: str = "0.0.0.0"
    http_port: int = 8080

    @property
    def azure_models_base_url(self) -> str:
        """Base des modèles non-OpenAI (rerank) : l'endpoint sans le suffixe /openai/v1."""
        return self.azure_ai_endpoint.removesuffix("/openai/v1").rstrip("/")


@lru_cache
def get_settings() -> Settings:
    """Construit (une seule fois par process, grâce au cache) l'instance ``Settings``.

    Lit ``.env`` puis les variables d'environnement réelles. Le cache évite de
    reparser l'environnement à chaque appel — utile car ``get_settings()`` est
    appelé depuis de nombreux points (scripts, app.py, tests) sans qu'un objet
    ``Settings`` unique ne soit explicitement propagé partout.
    """
    return Settings()
