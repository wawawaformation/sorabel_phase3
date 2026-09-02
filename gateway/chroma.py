"""Accès au serveur Chroma, partagé par l'ingestion et le retrieval."""

from urllib.parse import urlparse

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from gateway.settings import Settings


def chroma_client(settings: Settings) -> ClientAPI:
    parsed = urlparse(settings.chroma_url)
    return chromadb.HttpClient(
        host=parsed.hostname or "localhost", port=parsed.port or 8000
    )


def open_collection(client: ClientAPI, name: str) -> Collection:
    """Ouvre ou crée la collection, sans fonction d'embedding.

    Aucune embedding_function n'est passée : les vecteurs sont toujours fournis
    explicitement. Laisser Chroma calculer déclencherait le téléchargement de son
    modèle ONNX par défaut.
    """
    return client.get_or_create_collection(name=name, embedding_function=None)
