"""Calcul des embeddings via Azure AI Foundry (endpoint compatible OpenAI).

Partagé : l'ingestion vectorise les documents (ingest/), le retrieval vectorise la
question de l'utilisateur (retrieval/dense.py). Le protocole ``Embedder`` existe pour
que les pipelines soient testables sans réseau ni clé d'API — les tests injectent un
embedder factice qui retourne des vecteurs fixes plutôt que d'appeler Azure. L'objet
concret, ``AzureEmbedder``, encapsule le client OpenAI et le nom du modèle configuré ;
``embed_in_batches`` découpe une longue liste de textes pour rester sous la limite de
tokens par appel de l'API.
"""

from typing import Protocol

from openai import OpenAI

from gateway.settings import Settings

BATCH_SIZE = 64


class Embedder(Protocol):
    """Contrat minimal attendu par l'ingestion et le retrieval : une liste de textes
    en entrée, une liste de vecteurs de même longueur en sortie, dans le même ordre.
    N'importe quelle implémentation (réelle ou factice de test) le respectant est
    interchangeable partout où un ``Embedder`` est injecté.
    """

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class AzureEmbedder:
    """Embedder réel, adossé au client OpenAI pointé sur Azure AI Foundry.

    La dimension du vecteur retourné est celle du modèle configuré
    (``settings.azure_model_text_embedding_small`` — 1536 pour text-embedding-3-small)
    et n'est jamais vérifiée ici : Chroma la déduit du premier vecteur inséré.
    """

    def __init__(self, settings: Settings) -> None:
        self._client = OpenAI(
            base_url=settings.azure_ai_endpoint,
            api_key=settings.azure_ai_api_key,
        )
        self._model = settings.azure_model_text_embedding_small

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Un seul appel HTTP pour tous les textes fournis (l'API accepte un batch)."""
        response = self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in response.data]


def embed_in_batches(
    embedder: Embedder, texts: list[str], batch_size: int = BATCH_SIZE
) -> list[list[float]]:
    """Vectorise une longue liste de textes en la découpant en lots de ``batch_size``.

    Nécessaire pour l'ingestion (des centaines de chunks d'un coup) : l'API Azure
    limite le nombre de textes par appel, et un unique gros batch échouerait ou
    serait rejeté. ``retrieval/dense.py`` n'en a pas besoin (une seule question à la
    fois) et appelle directement ``embedder.embed([...])``.
    """
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(embedder.embed(texts[start : start + batch_size]))
    return vectors
