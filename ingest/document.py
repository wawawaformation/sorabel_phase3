"""Modèle du document canonique — normalisation du corpus hétérogène avant chunking.

Conception : conception/1_RAG/modele_document_canonique.py,
conception/1_RAG/questions_reponses_rag.md.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class DocumentCanonique(BaseModel):
    # --- Identité & regroupement ---
    document_id: str                  # identifie une version précise du document
    family_id: str                    # regroupe les versions d'un même document
    diversification_group: str        # regroupe les quasi-doublons, évite un top-k redondant

    # --- Contenu ---
    content: str                      # texte extrait et normalisé, avant chunking

    # --- Métadonnées descriptives ---
    title: str                        # titre du document (global) — pour citation (E1), pas les titres de sections/headings internes
    type_doc: Literal["fiche_technique", "notice", "procedure_sav", "note_interne"]
    collection: Literal["fiches", "notices", "sav", "notes"]  # filtrage par profil
    ref_produit: str | None = Field(default=None, pattern=r"^REF-\d{4}$")
    # référence principale du document — signal fort E2 ; None pour les procédures SAV
    version: str                      # numéro de version du document
    date: date                        # date du document — citation (E1), tri des versions
    source: Literal["pdf", "html", "md"]  # format d'origine avant normalisation
