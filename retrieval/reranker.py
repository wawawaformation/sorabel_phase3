"""Rerank Cohere via Azure AI Foundry — dernier étage du pipeline, seul à fournir un
score de pertinence absolu (contrairement au score RRF, relatif à une seule requête
et non comparable entre deux questions — voir docs/schemas/rerank_reel.drawio).

La route est POST {endpoint sans /openai/v1}/models/v1/rerank, en-tête api-key, format
Cohere v1 (vérifié — spec § 2.1, 14 combinaisons route/en-tête testées avant de trouver
la bonne). Le SDK openai ne sait pas appeler cette opération : c'est pourquoi ce module
utilise httpx directement plutôt que le client OpenAI déjà présent dans gateway/.
"""

from dataclasses import dataclass
from typing import Protocol

import httpx

from gateway.settings import Settings

RERANK_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class RerankResult:
    """Un document réordonné par le reranker, avec son score et sa position d'origine."""

    index: int  # position dans la liste documents envoyée
    score: float  # score absolu dans [0, 1]


class Reranker(Protocol):
    """Contrat minimal : requête + documents candidats → sous-ensemble ordonné et scoré.

    Permet à ``SearchEngine`` (retrieval/engine.py) de fonctionner sans reranker réel
    en test (double factice) ou en démo (``rerank_enabled=False``, spec § 4.3).
    """

    def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[RerankResult]: ...


class AzureCohereReranker:
    """Implémentation réelle : un appel HTTP direct à l'API Cohere hébergée sur Azure."""

    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        self._url = f"{settings.azure_models_base_url}/models/v1/rerank"
        self._model = settings.azure_model_reranking
        self._headers = {
            "Content-Type": "application/json",
            "api-key": settings.azure_ai_api_key,
        }
        self._client = http_client or httpx.Client(timeout=RERANK_TIMEOUT_S)

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankResult]:
        """Envoie les candidats à Cohere et retourne les ``top_n`` mieux classés.

        Un seul appel HTTP pour tous les documents. L'API trie déjà par score
        décroissant dans sa réponse — pas de tri supplémentaire côté client.
        Retourne une liste vide sans appel réseau si ``documents`` est vide (évite un
        appel facturé pour rien, par exemple si l'étage précédent n'a rien trouvé).
        """
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
