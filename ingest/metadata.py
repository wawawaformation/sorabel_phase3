"""Dérivation des identifiants et métadonnées à partir du chemin du fichier.

Règles vérifiées sur les 400 fichiers du corpus : 350 familles, 10 thèmes SAV,
5 thèmes de notes, aucune dérivation en échec.
"""

import re
from pathlib import Path
from typing import Literal, cast, get_args

from ingest.errors import IngestionError

# Alias reprenant à l'identique les Literal de DocumentCanonique et Chunk.
CollectionName = Literal["fiches", "notices", "sav", "notes"]
TypeDoc = Literal["fiche_technique", "notice", "procedure_sav", "note_interne"]
Source = Literal["pdf", "html", "md"]

TYPE_DOC_BY_COLLECTION: dict[CollectionName, TypeDoc] = {
    "fiches": "fiche_technique",
    "notices": "notice",
    "sav": "procedure_sav",
    "notes": "note_interne",
}

RE_VERSION_SUFFIX = re.compile(r"-v[\d.]+$")
RE_SAV_THEME = re.compile(r"^proc-(?P<theme>.+?)-\d+$")
RE_NOTE_THEME = re.compile(r"^note-\d{4}-\d{2}-\d{2}-(?P<theme>.+?)-\d+$")


def document_id(path: Path) -> str:
    """Nom du fichier sans extension. Unicité vérifiée sur les 400 fichiers."""
    return path.stem


def collection_of(path: Path) -> CollectionName:
    """Collection = nom du dossier parent, validé contre les valeurs attendues.

    C'est le seul endroit où une chaîne venue du système de fichiers devient un
    Literal : la validation a lieu ici, une fois, plutôt qu'un cast aveugle plus loin.
    """
    name = path.parent.name
    if name not in get_args(CollectionName):
        raise IngestionError(path, f"collection inconnue : {name}")
    return cast(CollectionName, name)


def type_doc_of(collection: CollectionName) -> TypeDoc:
    return TYPE_DOC_BY_COLLECTION[collection]


def family_id(doc_id: str) -> str:
    """Regroupe les versions d'un même document logique."""
    return RE_VERSION_SUFFIX.sub("", doc_id)


def diversification_group(collection: CollectionName, family: str, path: Path) -> str:
    """Regroupe les quasi-doublons métier (pas les versions, voir family_id)."""
    if collection == "sav":
        match = RE_SAV_THEME.match(family)
        if match is None:
            raise IngestionError(path, f"thème SAV non dérivable de : {family}")
        return f"sav_{match.group('theme')}"
    if collection == "notes":
        match = RE_NOTE_THEME.match(family)
        if match is None:
            raise IngestionError(path, f"thème de note non dérivable de : {family}")
        return f"note_{match.group('theme')}"
    # fiches et notices : deux produits distincts ne sont pas des quasi-doublons.
    return family
