"""Erreurs de l'ingestion."""

from pathlib import Path


class IngestionError(Exception):
    """Champ obligatoire manquant ou corpus incohérent.

    L'ingestion échoue fort : le corpus est régulier sur ses 400 fichiers, donc une
    extraction qui échoue signale une hypothèse devenue fausse, pas un cas limite à
    contourner silencieusement.
    """

    def __init__(self, path: Path | str, detail: str) -> None:
        super().__init__(f"{path} : {detail}")
        self.path = str(path)
        self.detail = detail
