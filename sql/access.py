"""Règles d'accès par profil — source de vérité unique du filtrage colonne × profil.

La conception (§ 3.3) impose que cette politique ne soit pas recopiée indépendamment
dans plusieurs fonctions : elle est consommée par ``sql/schema.py`` (filtrage du schéma
présenté), ``sql/guard.py`` (authorizer SQLite) et ``sql/engine.py`` (vérification des
références déclarées par le modèle). D'où le ``Protocol`` : le Chantier 3 pourra injecter
une implémentation adossée à la matrice d'accès MCP formelle sans modifier ``sql/``.
"""

from typing import Protocol

#: Profils métier du projet. Deux suffisent (brief).
PROFILES = frozenset({"support", "commercial"})

#: Colonnes que le profil ``support`` ne doit jamais voir — ni dans le schéma présenté,
#: ni dans le SQL accepté, ni dans le résultat (conception § 3.3, vérifié sur la base).
SENSITIVE_COLUMNS = frozenset({
    ("produits", "prix_achat_ht"),
    ("produits", "marge_pct"),
    ("ventes", "marge_ht"),
})


class AccessRules(Protocol):
    """Contrat minimal : quelles colonnes sont interdites à un profil donné.

    Le profil est passé en argument (et non porté par l'objet) pour qu'une seule
    instance de règles puisse servir plusieurs profils — c'est ``SqlEngine`` qui est
    lié à un profil, pas les règles.
    """

    def hidden_columns(self, profile: str) -> frozenset[tuple[str, str]]: ...


class StaticAccessRules:
    """Implémentation par défaut de ce chantier : la règle connue, en dur mais isolée.

    « En dur » ici veut dire « constante versionnée dans le dépôt », pas « dispersée
    dans le code métier » : un seul endroit à changer, et l'injection permet de la
    remplacer sans toucher aux consommateurs.
    """

    def hidden_columns(self, profile: str) -> frozenset[tuple[str, str]]:
        if profile not in PROFILES:
            raise ValueError(f"profil inconnu : {profile!r}")
        return SENSITIVE_COLUMNS if profile == "support" else frozenset()
