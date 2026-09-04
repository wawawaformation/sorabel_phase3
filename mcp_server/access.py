"""Matrice d'accès Sorabel — source déclarative unique, chargée une fois au démarrage.

Le fichier YAML (``matrice_acces.yaml``) est la présentation humaine et la source de
vérité machine à la fois (conception § 1.3 : « aucune permission n'est écrite deux
fois »). ``sql/access.py`` reste le seul ``Protocol`` que ``SqlEngine`` consomme
(``hidden_columns``) — ``YamlAccessRules`` l'implémente, en plus des deux dimensions
supplémentaires (``tools``, ``rag_collections``) qui n'ont aujourd'hui aucune
restriction réelle (spec_mcp.md § 2, point 4) mais restent déclarées pour rester
extensibles sans re-concevoir la matrice si un vrai besoin apparaît.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from sql.access import PROFILES

DEFAULT_MATRIX_PATH = Path(__file__).parent / "matrice_acces.yaml"


@dataclass(frozen=True)
class ProfileRules:
    tools: frozenset[str]
    rag_collections: frozenset[str]
    hidden_columns: frozenset[tuple[str, str]]


def load_matrix(path: Path = DEFAULT_MATRIX_PATH) -> dict[str, ProfileRules]:
    """Charge et valide la matrice : un profil manquant ou en trop est une erreur au
    démarrage, jamais un accès silencieusement ouvert ou fermé."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    declared = set(raw["profiles"])
    if declared != set(PROFILES):
        raise ValueError(
            f"profils de la matrice {sorted(declared)} != attendus {sorted(PROFILES)}"
        )
    matrix: dict[str, ProfileRules] = {}
    for profile, data in raw["profiles"].items():
        matrix[profile] = ProfileRules(
            tools=frozenset(data["tools"]),
            rag_collections=frozenset(data["rag_collections"]),
            hidden_columns=frozenset(tuple(pair) for pair in data["sql_hidden_columns"]),
        )
    return matrix


class YamlAccessRules:
    """Implémente ``sql.access.AccessRules`` ; expose aussi les dimensions tool et
    collection consommées par ``mcp_server/catalogue.py`` (``_meta["sorabel/roles"]``)."""

    def __init__(self, matrix: dict[str, ProfileRules]) -> None:
        self._matrix = matrix

    def hidden_columns(self, profile: str) -> frozenset[tuple[str, str]]:
        return self._rules(profile).hidden_columns

    def allowed_tools(self, profile: str) -> frozenset[str]:
        return self._rules(profile).tools

    def allowed_collections(self, profile: str) -> frozenset[str]:
        return self._rules(profile).rag_collections

    def _rules(self, profile: str) -> ProfileRules:
        if profile not in self._matrix:
            raise ValueError(f"profil inconnu : {profile!r}")
        return self._matrix[profile]
