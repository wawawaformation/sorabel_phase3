"""Barrières de lecture seule : connexions, authorizer SQLite.

Trois mécanismes cumulés, chacun couvrant un risque que les autres ne couvrent pas
(conception § 2.2, « aucune barrière suffisante isolément ») :

1. ``mode=ro`` — la connexion est ouverte en lecture seule ;
2. ``PRAGMA query_only`` — défense complémentaire au niveau de la connexion ;
3. ``set_authorizer()`` — contrôle ce que SQLite cherche réellement à faire, avec le
   grain de la colonne.

Rappel de la conception § 2.9, qui reste vrai malgré tout ce module : ces trois
mécanismes sont **par connexion**, pas par fichier. Seule la permission fichier au
niveau du système d'exploitation empêche une deuxième connexion non protégée d'écrire.
C'est une condition de déploiement, pas du code.

Deux fonctions d'ouverture, parce que deux usages ont des besoins incompatibles :
l'introspection a besoin de ``PRAGMA`` (que l'authorizer refuse), l'exécution a besoin
de l'authorizer. Cette séparation n'affaiblit rien — la connexion d'introspection
n'exécute que du SQL écrit par nous, jamais du SQL généré par un modèle (spec § 2.5).
"""

import sqlite3
from pathlib import Path

from sql.access import AccessRules

#: Seuls codes d'action nécessaires au SQL de lecture légitime, déterminés en
#: instrumentant dix formes de requêtes réelles : simple, agrégat, COUNT, jointure,
#: GROUP BY + ORDER BY, sous-requête, CTE, fonctions de texte et de date, triple
#: jointure (spec § 2.3). Tout le reste est refusé par défaut.
#: SQLITE_RECURSIVE en est volontairement absent : aucune question du jeu d'évaluation
#: n'a besoin d'une CTE récursive, et borner la complexité générée est un bénéfice.
ALLOWED_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
})


def open_introspection(path: Path) -> sqlite3.Connection:
    """Connexion dédiée à la lecture du schéma : ``PRAGMA`` autorisé, pas d'authorizer.

    Sans authorizer parce que ``PRAGMA`` déclenche ``SQLITE_PRAGMA``, hors allowlist
    (vérifié, spec § 2.5). Le risque est nul : cette connexion n'exécute que des
    ``PRAGMA`` dont notre code écrit le texte, et reste en lecture seule.
    """
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def open_execution(
    path: Path, access_rules: AccessRules, profile: str
) -> sqlite3.Connection:
    """Connexion d'exécution du SQL généré ou figé : lecture seule et authorizer strict.

    L'authorizer fonctionne en **allowlist** : il refuse par défaut et n'autorise que
    les trois codes d'``ALLOWED_ACTIONS``. C'est délibéré — une liste noire laisse
    passer ce qu'on n'a pas pensé à énumérer, et un authorizer qui ne refusait que des
    colonnes sensibles laissait effectivement passer les ``UPDATE`` (spec § 2.3).
    """
    hidden = access_rules.hidden_columns(profile)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")  # avant l'authorizer, qui refuse PRAGMA

    def authorize(action: int, arg1: str | None, arg2: str | None,
                  db_name: str | None, trigger: str | None) -> int:
        if action == sqlite3.SQLITE_READ:
            table = arg1 or ""
            # Les tables internes de SQLite exposent le CREATE TABLE complet, donc
            # l'existence des colonnes sensibles : fuite vérifiée (spec § 2.4).
            if table.startswith("sqlite_"):
                return sqlite3.SQLITE_DENY
            if (table, arg2 or "") in hidden:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        # SQLITE_DENY et jamais SQLITE_IGNORE : IGNORE produirait un résultat
        # silencieusement tronqué, plus trompeur qu'une erreur (conception § 3.7).
        return sqlite3.SQLITE_OK if action in ALLOWED_ACTIONS else sqlite3.SQLITE_DENY

    connection.set_authorizer(authorize)
    return connection
