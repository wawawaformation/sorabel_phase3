"""Ingère le corpus documentaire dans Chroma.

Usage : ``make ingest`` ou ``uv run python scripts/run_ingest.py``.
Nécessite Chroma (``make up``) et les variables Azure de ``.env``.

Nommé run_ingest.py (pas ingest.py) : ce dernier, exécuté directement, se
retrouverait en tête de sys.path et masquerait le paquet ingest/ du même nom.
"""

from gateway.chroma import chroma_client, open_collection
from gateway.embedder import AzureEmbedder
from gateway.settings import get_settings
from ingest.pipeline import ingest_corpus


def main() -> None:
    settings = get_settings()
    collection = open_collection(
        chroma_client(settings), settings.chroma_collection
    )
    written = ingest_corpus(
        settings.corpus_dir, collection, AzureEmbedder(settings)
    )
    print(f"{written} chunks ingérés dans « {settings.chroma_collection} »")


if __name__ == "__main__":
    main()
