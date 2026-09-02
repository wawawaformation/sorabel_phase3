"""Assemblage : fichier source → DocumentCanonique."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

from ingest.document import DocumentCanonique
from ingest.errors import IngestionError
from ingest.extract import Extracted, extract_html, extract_markdown, extract_pdf
from ingest.metadata import (
    Source,
    collection_of,
    diversification_group,
    document_id,
    family_id,
    type_doc_of,
)

EXTRACTORS_BY_SUFFIX: dict[str, Callable[[Path], Extracted]] = {
    ".pdf": extract_pdf,
    ".html": extract_html,
    ".md": extract_markdown,
}

SOURCE_BY_SUFFIX: dict[str, Source] = {".pdf": "pdf", ".html": "html", ".md": "md"}


def build_document(path: Path) -> DocumentCanonique:
    suffix = path.suffix.lower()
    extractor = EXTRACTORS_BY_SUFFIX.get(suffix)
    if extractor is None:
        raise IngestionError(path, f"format non pris en charge : {suffix}")

    extracted = extractor(path)
    doc_id = document_id(path)
    collection = collection_of(path)
    family = family_id(doc_id)
    return DocumentCanonique(
        document_id=doc_id,
        family_id=family,
        diversification_group=diversification_group(collection, family, path),
        content=extracted.text,
        title=extracted.title,
        type_doc=type_doc_of(collection),
        collection=collection,
        ref_produit=extracted.ref_produit,
        version=extracted.version,
        date=date.fromisoformat(extracted.date),
        source=SOURCE_BY_SUFFIX[suffix],
    )
