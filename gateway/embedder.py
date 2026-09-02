"""Calcul des embeddings via Azure AI Foundry (endpoint compatible OpenAI).

Partagé : l'ingestion vectorise les documents, le retrieval vectorise la question.
Le protocole Embedder existe pour que les pipelines soient testables sans réseau ni
clé d'API — les tests injectent un embedder factice.
"""

from typing import Protocol

from openai import OpenAI

from gateway.settings import Settings

BATCH_SIZE = 64


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class AzureEmbedder:
    """Embedder Azure. La dimension du vecteur est celle que renvoie le modèle."""

    def __init__(self, settings: Settings) -> None:
        self._client = OpenAI(
            base_url=settings.azure_ai_endpoint,
            api_key=settings.azure_ai_api_key,
        )
        self._model = settings.azure_model_text_embedding_small

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in response.data]


def embed_in_batches(
    embedder: Embedder, texts: list[str], batch_size: int = BATCH_SIZE
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(embedder.embed(texts[start : start + batch_size]))
    return vectors
