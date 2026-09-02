"""Accès au serveur Chroma, partagé par l'ingestion et le retrieval.

Deux fonctions courtes mais volontairement séparées : ``chroma_client`` ouvre la
connexion HTTP vers le serveur (lancé via ``make up``, docker compose), et
``open_collection`` récupère ou crée la collection nommée dedans. Cette séparation
permet aux tests d'intégration d'utiliser un ``chromadb.EphemeralClient()`` en
mémoire à la place de ``chroma_client`` tout en réutilisant ``open_collection``
tel quel.
"""

from urllib.parse import urlparse

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection

from gateway.settings import Settings


def chroma_client(settings: Settings) -> ClientAPI:
    """Ouvre une connexion HTTP vers le serveur Chroma décrit par ``settings.chroma_url``.

    Parse l'URL plutôt que de la passer telle quelle : l'API ``HttpClient`` de
    chromadb attend un host et un port séparés, pas une URL complète.
    """
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
