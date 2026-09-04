"""Ingère le corpus si (et seulement si) l'index Chroma est vide.

Distinct de scripts/run_ingest.py (qui ingère toujours) : celui-ci est fait pour
tourner à chaque démarrage du conteneur gateway (docker/entrypoint.sh), sans jamais
ré-ingérer un index déjà peuplé.
"""

from gateway.chroma import chroma_client, open_collection
from gateway.embedder import AzureEmbedder
from gateway.settings import get_settings
from ingest.pipeline import ingest_corpus


def main() -> None:
    settings = get_settings()
    collection = open_collection(chroma_client(settings), settings.chroma_collection)
    if collection.count() > 0:
        print(f"« {settings.chroma_collection} » déjà peuplée "
              f"({collection.count()} chunks) — rien à faire")
        return
    written = ingest_corpus(settings.corpus_dir, collection, AzureEmbedder(settings))
    print(f"{written} chunks ingérés dans « {settings.chroma_collection} »")


if __name__ == "__main__":
    main()
