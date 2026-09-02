"""Extraction du texte et des métadonnées, une fonction par format du corpus.

Aucun LLM : les métadonnées sont structurées (regex sur les PDF, attributs pour le
HTML, front-matter pour le Markdown), jamais devinées.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from ingest.errors import IngestionError

RE_PDF_TITLE = re.compile(r"^(?:FICHE TECHNIQUE|NOTICE D'INSTALLATION)\s*-\s*(.+)$", re.M)
RE_PDF_REF = re.compile(r"R[ée]f[ée]rence produit\s*:\s*(REF-\d{4})")
RE_PDF_VERSION = re.compile(r"Version\s*:\s*([\d.]+)")
RE_PDF_DATE = re.compile(r"Date\s*:\s*(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Extracted:
    """Résultat brut d'une extraction, avant dérivation des identifiants."""

    text: str
    title: str
    version: str
    date: str
    ref_produit: str | None


def _require(pattern: re.Pattern[str], text: str, path: Path, field: str) -> str:
    match = pattern.search(text)
    if match is None:
        raise IngestionError(path, f"champ obligatoire introuvable : {field}")
    return match.group(1).strip()


def extract_pdf(path: Path) -> Extracted:
    """Extrait une fiche technique ou une notice.

    Les PDF du corpus ne portent aucune métadonnée embarquée : tout vient du texte.
    Les regex s'appliquent au texte entier, sans hypothèse de position de ligne — la
    mise en page diffère entre fiches et notices.
    """
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return Extracted(
        text=text.strip(),
        title=_require(RE_PDF_TITLE, text, path, "title"),
        version=_require(RE_PDF_VERSION, text, path, "version"),
        date=_require(RE_PDF_DATE, text, path, "date"),
        ref_produit=_require(RE_PDF_REF, text, path, "ref_produit"),
    )
