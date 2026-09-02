"""Configuration partagée par l'ingestion, le retrieval et (plus tard) le serveur MCP."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" : .env porte aussi des variables d'autres modules
    # (SORABEL_PROFILE, GATEWAY_JOURNAL…).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Corpus & index ---
    corpus_dir: Path = Path("data/corpus")
    chroma_url: str = "http://localhost:8002"
    chroma_collection: str = "sorabel_corpus"

    # --- Azure AI Foundry ---
    azure_ai_endpoint: str = ""
    azure_ai_api_key: str = ""
    azure_model_text_embedding_small: str = "text-embedding-3-small"
    azure_model_reranking: str = "Cohere-rerank-v4.0-pro"
    azure_model_text_generation: str = "gpt-5.4-mini"

    # --- Retrieval ---
    rerank_enabled: bool = True
    # Calibré sur eval/questions_rag.jsonl (scripts/eval_rag.py, voir eval/rapport_gain.md) :
    # max(hors corpus)=0.626, min(couvertes)=0.669, séparation parfaite entre 0.64 et 0.66.
    refusal_threshold: float = 0.65
    dense_candidates: int = 30
    lexical_candidates: int = 30
    fusion_candidates: int = 20
    rerank_candidates: int = 10
    top_k: int = 5
    rrf_k: int = 60

    @property
    def azure_models_base_url(self) -> str:
        """Base des modèles non-OpenAI (rerank) : l'endpoint sans le suffixe /openai/v1."""
        return self.azure_ai_endpoint.removesuffix("/openai/v1").rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
