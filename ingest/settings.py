"""Configuration de l'ingestion, lue depuis l'environnement ou .env.

À déplacer dans un module partagé quand retrieval/ et mcp_server/ en auront besoin.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" : .env porte aussi des variables d'autres modules
    # (SORABEL_PROFILE, GATEWAY_JOURNAL, AZURE_MODEL_RERANKING…).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    corpus_dir: Path = Path("data/corpus")
    chroma_url: str = "http://localhost:8002"
    chroma_collection: str = "sorabel_corpus"
    azure_ai_endpoint: str = ""
    azure_ai_api_key: str = ""
    azure_model_text_embedding_small: str = "text-embedding-3-small"


@lru_cache
def get_settings() -> Settings:
    return Settings()
