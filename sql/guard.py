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
import time
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


#: Mots-clés d'écriture ou de modification de schéma. Sert deux fois : au refus du SQL
#: généré (``validate_sql``) et à la détection d'intention dans la question posée, avant
#: même l'appel au modèle (``sql/generate.py``, spec § 4.11).
#:
#: Faux positif connu et assumé : ``replace`` est aussi une fonction SQLite légitime
#: (``replace(nom, 'a', 'b')``), qui serait donc refusée. Aucune des 24 questions du jeu
#: d'évaluation n'en a besoin, et pencher du côté strict est le bon compromis ici — mais
#: c'est bien une limite, pas une propriété.
WRITE_KEYWORDS = frozenset({
    "insert", "update", "delete", "drop", "alter", "create",
    "replace", "attach", "detach", "truncate", "vacuum", "reindex",
})

#: Repérage grossier d'une agrégation : ces requêtes retournent naturellement peu de
#: lignes, un LIMIT n'y apporterait rien (conception § 2.7).
_AGGREGATE_HINTS = ("count(", "sum(", "avg(", "min(", "max(", "total(", "group_concat(")


class ValidationError(Exception):
    """SQL refusé avant d'atteindre la base, pour une raison structurale."""


def _normalize(sql: str) -> str:
    """Minuscules, espaces resserrés, point-virgule final retiré."""
    return " ".join(sql.lower().split()).rstrip(";").strip()


def validate_sql(sql: str) -> None:
    """Refuse tout ce qui n'est pas une lecture unique et explicite.

    Cette validation double des barrières que SQLite applique déjà (authorizer,
    ``mode=ro``) — c'est volontaire : elle produit un refus motivé et traçable *avant*
    d'atteindre la base, là où l'authorizer ne fournit qu'une exception technique. Elle
    fonctionne en allowlist de formes (`SELECT`, `WITH … SELECT`), la liste de mots-clés
    ne venant qu'en complément (conception § 2.3).
    """
    normalise = _normalize(sql)
    if not normalise:
        raise ValidationError("requête vide")
    if ";" in normalise:
        raise ValidationError("une seule instruction SQL est acceptée")
    # Les mots-clés d'écriture sont testés AVANT la forme : un « INSERT … » doit être
    # refusé pour ce qu'il est (une écriture), pas pour ne pas commencer par SELECT —
    # le motif du refus part dans la trace, il doit être juste.
    mots = set(normalise.replace("(", " ").replace(")", " ").replace(",", " ").split())
    interdits = mots & WRITE_KEYWORDS
    if interdits:
        raise ValidationError(f"mot-clé d'écriture interdit : {', '.join(sorted(interdits))}")
    if not (normalise.startswith("select ") or normalise.startswith("with ")):
        raise ValidationError("seules les formes SELECT et WITH … SELECT sont acceptées")
    if "select *" in normalise or "select  *" in normalise:
        raise ValidationError("SELECT * interdit : colonnes toujours explicites")


def apply_limit(sql: str, limit: int) -> str:
    """Ajoute ``LIMIT limit + 1`` si la requête n'a pas de limite et n'est pas agrégée.

    Le ``+ 1`` est la ruse qui rend la troncature détectable : recevoir exactement
    ``limit`` lignes ne dit pas s'il y en avait davantage (spec § 2.10). ``run_query``
    ne retourne ensuite que ``limit`` lignes au plus.
    """
    normalise = _normalize(sql)
    if " limit " in f" {normalise} " or any(h in normalise for h in _AGGREGATE_HINTS):
        return sql
    return f"{sql.rstrip().rstrip(';')} LIMIT {limit + 1}"


def run_query(
    connection: sqlite3.Connection, sql: str, timeout_s: float, limit: int
) -> tuple[list[str], list[tuple], bool]:
    """Exécute la requête sous délai maximal et retourne (colonnes, lignes, tronqué).

    Le délai s'appuie sur ``set_progress_handler`` : un callback appelé tous les 1000
    opcodes de la machine virtuelle SQLite, qui interrompt en retournant une valeur non
    nulle. ``PRAGMA busy_timeout`` ne conviendrait pas — il ne concerne que l'attente
    d'un verrou, pas la durée d'un calcul (vérifié en conception § 2.7).

    Le handler est retiré en sortie, y compris en cas d'erreur : une connexion
    réutilisée ne doit pas hériter du délai d'une requête précédente.
    """
    deadline = time.monotonic() + timeout_s

    def interrupt_if_late() -> int:
        return 1 if time.monotonic() > deadline else 0

    connection.set_progress_handler(interrupt_if_late, 1000)
    try:
        cursor = connection.execute(sql)
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description] if cursor.description else []
    finally:
        connection.set_progress_handler(None, 0)

    truncated = len(rows) > limit
    return columns, rows[:limit], truncated
