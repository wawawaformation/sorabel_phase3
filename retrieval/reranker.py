"""Rerank Cohere via Azure AI Foundry.

La route est POST {endpoint sans /openai/v1}/models/v1/rerank, en-tête api-key, format
Cohere v1 (vérifié — spec § 2.1). Le SDK openai ne sait pas appeler cette opération.
"""

from dataclasses import dataclass
from typing import Protocol

import httpx

from gateway.settings import Settings

RERANK_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class RerankResult:
    index: int  # position dans la liste documents envoyée
    score: float  # score absolu dans [0, 1]


class Reranker(Protocol):
    def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankResult]: ...


class AzureCohereReranker:
    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        self._url = f"{settings.azure_models_base_url}/models/v1/rerank"
        self._model = settings.azure_model_reranking
        self._headers = {
            "Content-Type": "application/json",
            "api-key": settings.azure_ai_api_key,
        }
        self._client = http_client or httpx.Client(timeout=RERANK_TIMEOUT_S)

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankResult]:
        """Retourne les documents réordonnés, score décroissant (l'API trie déjà)."""
        if not documents:
            return []
        payload = {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
        }
        response = self._client.post(self._url, json=payload, headers=self._headers)
        response.raise_for_status()
        return [
            RerankResult(index=int(item["index"]), score=float(item["relevance_score"]))
            for item in response.json()["results"]
        ]
