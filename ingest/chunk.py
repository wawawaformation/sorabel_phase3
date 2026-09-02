"""Modèle du chunk RAG — unité indexée dans Chroma.

Conception : conception/1_RAG/modele_chunk.py,
conception/1_RAG/questions_reponses_rag.md § 2.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    # --- Identité & regroupement ---
    chunk_id: str                     # identifie ce fragment
    document_id: str                  # lien obligatoire vers le document parent
    chunk_index: int                  # vaut 0 sur le corpus actuel (1 chunk = 1 document entier) ; ne redevient une position de fragment qu'en cas de repli structurel non déclenché, voir questions_reponses_rag.md § 2
    family_id: str                    # regroupe les versions d'un même document
    diversification_group: str        # regroupe les quasi-doublons, évite un top-k redondant

    # --- Contenu ---
    content: str                      # texte brut, stocké et retourné tel quel (citations, BM25) ; le vecteur dense seul est calculé sur title + ref_produit + content, jamais content stocké modifié

    # --- Métadonnées descriptives (héritées du document) ---
    title: str                        # titre du document parent (DocumentCanonique) — pour citation (E1), pas les titres de sections/headings
    type_doc: Literal["fiche_technique", "notice", "procedure_sav", "note_interne"]
    collection: Literal["fiches", "notices", "sav", "notes"]  # filtrage par profil
    ref_produit: str | None = Field(default=None, pattern=r"^REF-\d{4}$")
    # référence principale du document — signal fort E2 ; None pour les procédures SAV
    version: str                      # numéro de version du document
    date: date                        # date du document — citation (E1), tri des versions
    source: Literal["pdf", "html", "md"]  # format d'origine avant normalisation
