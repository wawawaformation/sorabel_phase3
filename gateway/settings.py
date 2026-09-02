"""Configuration partagée par l'ingestion, le retrieval et (plus tard) le serveur MCP."""

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

    @property
    def azure_models_base_url(self) -> str:
        """Base des modèles non-OpenAI (rerank) : l'endpoint sans le suffixe /openai/v1."""
        return self.azure_ai_endpoint.removesuffix("/openai/v1").rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
