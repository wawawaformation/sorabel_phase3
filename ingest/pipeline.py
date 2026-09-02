"""Orchestration de l'ingestion : corpus → chunks → embeddings → Chroma.

Le client Chroma et l'embedder sont injectés : c'est ce qui rend l'ingestion
complète testable sans Docker ni appel réseau.
"""

from collections import Counter
from pathlib import Path

from chromadb.api.models.Collection import Collection

from gateway.embedder import Embedder, embed_in_batches
from ingest.build import EXTRACTORS_BY_SUFFIX, build_document
from ingest.chunk import Chunk
from ingest.chunking import to_chunks
from ingest.embedder import embedding_text
from ingest.errors import IngestionError
from ingest.store import upsert_chunks


def iter_corpus_files(corpus_dir: Path) -> list[Path]:
    """Fichiers du corpus, triés, extensions prises en charge uniquement."""
    files = sorted(
        p for p in corpus_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in EXTRACTORS_BY_SUFFIX
    )
    duplicates = [k for k, n in Counter(p.stem for p in files).items() if n > 1]
    if duplicates:
        raise IngestionError(corpus_dir, f"document_id dupliqués : {duplicates}")
    return files


def ingest_corpus(
    corpus_dir: Path, collection: Collection, embedder: Embedder
) -> int:
    """Ingère tout le corpus et retourne le nombre de chunks écrits."""
    chunks: list[Chunk] = []
    for path in iter_corpus_files(corpus_dir):
        chunks.extend(to_chunks(build_document(path)))

    vectors = embed_in_batches(embedder, [embedding_text(c) for c in chunks])
    upsert_chunks(collection, chunks, vectors)
    return len(chunks)
