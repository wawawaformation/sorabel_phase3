"""Ingère le corpus documentaire dans Chroma.

Usage : ``make ingest`` ou ``uv run python scripts/ingest.py``.
Nécessite Chroma (``make up``) et les variables Azure de ``.env``.
"""

from ingest.embedder import AzureEmbedder
from ingest.pipeline import ingest_corpus
from ingest.settings import get_settings
from ingest.store import chroma_client, open_collection


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
