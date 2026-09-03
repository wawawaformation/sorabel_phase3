"""Lecture du schéma à la source, filtrée selon le profil — cœur de ``get_schema``.

La structure vient exclusivement de l'introspection SQLite (``PRAGMA table_info`` et
``PRAGMA foreign_key_list``) : ajouter une colonne à la base la fait apparaître ici sans
toucher au code Python, ce qui est l'exigence « lu à la source, jamais codé en dur ». Les
descriptions métier viennent de ``sql/descriptions.py``, parce que SQLite ne stocke aucun
commentaire (vérifié, spec § 2.2).

Attention, contrainte non évidente : ``PRAGMA`` est refusé par l'authorizer strict de
``sql/guard.py`` (vérifié, spec § 2.5). Les fonctions de ce module doivent donc recevoir
la connexion d'**introspection**, celle qui ne porte pas d'authorizer — jamais la
connexion d'exécution.
"""

import sqlite3
from dataclasses import dataclass

from sql.access import AccessRules
from sql.descriptions import COLUMN_DOCS, TABLE_DOCS

#: Numéro de mois -> nom français, pour la correspondance mois/millésime (§ 4.5 de la spec).
MONTH_NAMES = {
    "01": "janvier", "02": "février", "03": "mars", "04": "avril",
    "05": "mai", "06": "juin", "07": "juillet", "08": "août",
    "09": "septembre", "10": "octobre", "11": "novembre", "12": "décembre",
}


@dataclass(frozen=True)
class ColumnInfo:
    """Une colonne telle que présentée au modèle et au client : structure + sens."""

    name: str
    type: str
    description: str
    values: tuple[str, ...] | None


@dataclass(frozen=True)
class TableInfo:
    name: str
    description: str
    columns: tuple[ColumnInfo, ...]


@dataclass(frozen=True)
class SchemaResponse:
    """Réponse du tool ``get_schema`` : tables autorisées et relations entre elles."""

    tables: tuple[TableInfo, ...]
    relations: tuple[str, ...]


def _table_names(connection: sqlite3.Connection) -> list[str]:
    """Tables métier, par ordre alphabétique — les tables internes de SQLite exclues.

    Les ``sqlite_%`` sont écartées ici pour le schéma présenté, et refusées séparément
    à l'exécution par l'authorizer (spec § 2.4) : deux barrières, deux rôles.
    """
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def read_schema(
    connection: sqlite3.Connection, access_rules: AccessRules, profile: str
) -> SchemaResponse:
    """Construit le schéma visible par ce profil, structure introspectée et sens documenté.

    Lève ``KeyError`` si une colonne réelle n'a pas de description : une dérive
    silencieuse entre la base et sa documentation donnerait un contexte incomplet au
    modèle générateur, ce qui est plus dangereux qu'un échec visible (spec § 4.4).
    """
    hidden = access_rules.hidden_columns(profile)
    tables: list[TableInfo] = []
    relations: list[str] = []

    for table in _table_names(connection):
        columns: list[ColumnInfo] = []
        for row in connection.execute(f"PRAGMA table_info({table})"):
            column = str(row[1])
            if (table, column) in hidden:
                continue  # absente de la structure, pas seulement masquée
            doc = COLUMN_DOCS.get((table, column))
            if doc is None:
                raise KeyError(f"colonne sans description : {table}.{column}")
            columns.append(
                ColumnInfo(
                    name=column,
                    type=str(row[2]),
                    description=doc.description,
                    values=doc.values,
                )
            )
        tables.append(
            TableInfo(
                name=table,
                description=TABLE_DOCS.get(table, ""),
                columns=tuple(columns),
            )
        )
        for fk in connection.execute(f"PRAGMA foreign_key_list({table})"):
            relations.append(f"{table}.{fk[3]} -> {fk[2]}.{fk[4]}")

    return SchemaResponse(tables=tuple(tables), relations=tuple(sorted(relations)))


def covered_months(connection: sqlite3.Connection) -> dict[str, str]:
    """Millésime de chaque mois présent dans les commandes, quand il est unique.

    Répond au cas « combien de commandes en avril ? », posé sans année : plutôt que de
    laisser le modèle deviner (comportement instable, mesuré en spec § 2.12), le code
    calcule la correspondance et la lui donne comme un fait. Un mois présent sur deux
    millésimes est volontairement absent du résultat : il reste légitimement ambigu.
    """
    rows = connection.execute(
        "SELECT DISTINCT strftime('%m', date_commande), strftime('%Y', date_commande) "
        "FROM commandes"
    ).fetchall()
    years_by_month: dict[str, set[str]] = {}
    for month, year in rows:
        years_by_month.setdefault(str(month), set()).add(str(year))
    return {
        MONTH_NAMES[month]: next(iter(years))
        for month, years in years_by_month.items()
        if len(years) == 1 and month in MONTH_NAMES
    }


def schema_as_prompt(schema: SchemaResponse, months: dict[str, str]) -> str:
    """Rend le schéma en texte destiné au prompt de génération.

    Une seule fonction produit ce texte, à partir du schéma **déjà filtré** : c'est ce
    qui garantit qu'une colonne interdite ne peut pas réapparaître dans le contexte du
    modèle par une mise en forme parallèle (conception § 3.4).
    """
    blocks: list[str] = []
    for table in schema.tables:
        lignes = [f"TABLE {table.name}" + (f"  -- {table.description}" if table.description else "")]
        for column in table.columns:
            ligne = f"  {column.name} ({column.type}) : {column.description}"
            if column.values:
                ligne += f" Valeurs : {', '.join(column.values)}."
            lignes.append(ligne)
        blocks.append("\n".join(lignes))

    if schema.relations:
        blocks.append("Relations :\n" + "\n".join(f"  {r}" for r in schema.relations))

    if months:
        ordre = list(MONTH_NAMES.values())
        connus = sorted(months, key=ordre.index)
        blocks.append(
            "Millésime de chaque mois présent dans les données :\n"
            + "\n".join(f"  {mois} -> {months[mois]}" for mois in connus)
            + "\nUn mois cité sans année désigne le millésime ci-dessus. Un mois absent "
              "de cette liste est ambigu : demander une clarification."
        )

    return "\n\n".join(blocks)
