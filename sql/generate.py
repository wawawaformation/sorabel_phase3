"""Génération du SQL : classification et traduction en un seul appel structuré.

Le modèle reçoit un contexte contrôlé — le schéma **déjà filtré** par le profil, les
relations, les particularités métier, la correspondance mois/millésime — et retourne un
objet JSON conforme à un schéma strict. Un seul appel fait à la fois la classification
(la question est-elle traduisible, ambiguë, ou hors périmètre ?) et la génération : le
modèle ne produit du SQL que si le statut est ``SQL_GENERABLE`` (conception § 5.5).

Le modèle déclare aussi les tables et colonnes qu'il compte utiliser. Cette déclaration
est vérifiée par ``sql/engine.py`` contre le schéma filtré, avant même de regarder le
SQL : détecter une intention hors périmètre est plus simple sur une liste que sur une
syntaxe SQL avec alias, jointures et sous-requêtes (conception § 2.11). Elle ne remplace
rien en aval — le modèle peut déclarer honnêtement et générer autre chose.
"""

import json
from dataclasses import dataclass
from typing import Any, Literal

from sql.guard import WRITE_KEYWORDS

GenerationStatus = Literal["SQL_GENERABLE", "AMBIGUOUS", "OUT_OF_SCHEMA"]

MAX_SQL_TOKENS = 900

#: Sortie structurée stricte : vérifié comme supporté par gpt-5.4-mini (spec § 2.11).
#: Tous les champs sont requis — c'est une contrainte du mode strict — d'où les chaînes
#: vides pour les champs non pertinents à un statut donné.
RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "sql_generation",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["SQL_GENERABLE", "AMBIGUOUS", "OUT_OF_SCHEMA"],
            },
            "sql": {"type": "string"},
            "tables_referencees": {"type": "array", "items": {"type": "string"}},
            "colonnes_referencees": {"type": "array", "items": {"type": "string"}},
            "clarification": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": [
            "status", "sql", "tables_referencees", "colonnes_referencees",
            "clarification", "reason",
        ],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT_TEMPLATE = """\
Tu traduis une question métier en SQL SQLite, pour la base de Sorabel, distributeur de
matériel électrique.

{schema}

Règles impératives :
- Utilise UNIQUEMENT les tables et colonnes listées ci-dessus. Toute autre colonne
  n'existe pas ou n'est pas accessible à cet appelant.
- SELECT uniquement (ou WITH ... SELECT). Jamais INSERT, UPDATE, DELETE, DROP, ALTER,
  CREATE, REPLACE, ATTACH.
- Jamais SELECT * : colonnes toujours explicites.
- La table ventes n'a AUCUNE colonne de date : pour une question temporelle sur les
  ventes, joindre commandes et utiliser commandes.date_commande.
- Une référence produit a plusieurs lignes dans stocks, une par entrepôt : un stock
  total est une somme.
- status = SQL_GENERABLE : la question est traduisible sans deviner d'interprétation
  métier. Déclare alors dans tables_referencees et colonnes_referencees (format
  "table.colonne") tout ce que ton SQL utilise réellement, jointures comprises.
- status = AMBIGUOUS : la question est dans le périmètre mais un critère métier est
  indéfini (« le meilleur client », « ça se vend bien »). Propose une clarification,
  ne devine pas.
- status = OUT_OF_SCHEMA : la question ne concerne pas ces données, OU la donnée
  nécessaire n'existe pas parmi les colonnes listées (par exemple une marge ou un coût
  d'achat absent). Dans ce cas c'est OUT_OF_SCHEMA et non AMBIGUOUS : ne demande pas de
  clarification pour une donnée que tu n'as pas, et n'explique pas ce qui manque.
- Si status n'est pas SQL_GENERABLE : sql = "" et les deux listes sont vides.
- Champs non pertinents : chaîne vide.\
"""


@dataclass(frozen=True)
class Generation:
    """Sortie du modèle, décodée. ``sql`` est vide sauf si ``status`` vaut SQL_GENERABLE."""

    status: GenerationStatus
    sql: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    clarification: str
    reason: str


def looks_like_write(question: str) -> bool:
    """Détecte une intention d'écriture dans la question, avant tout appel au modèle.

    Deux bénéfices : un appel LLM économisé, et surtout une trace qui distingue une
    tentative de modification d'une simple question hors sujet — deux entrées de
    journal qui n'ont pas la même signification en audit (spec § 4.11).

    Ce n'est pas une barrière de sécurité : elle rate les formulations implicites. Le
    modèle reste le filet en aval, et les barrières de ``sql/guard.py`` restent la
    garantie réelle.
    """
    mots = set(question.lower().replace("'", " ").replace(".", " ").split())
    if mots & WRITE_KEYWORDS:
        return True
    verbes = {
        "supprime", "supprimer", "efface", "effacer", "vide", "vider",
        "insère", "insere", "insérer", "inserer", "ajoute", "ajouter",
        "modifie", "modifier", "mets", "mettre", "change", "changer",
        "remplace", "remplacer",
    }
    return bool(mots & verbes)


def generate_sql(client: Any, model: str, question: str, schema_prompt: str) -> Generation:
    """Un appel structuré unique : classification et génération à la fois.

    ``client`` a la forme du SDK openai (non typé strictement pour rester injectable en
    test). ``max_completion_tokens`` et non ``max_tokens`` : gpt-5.4-mini rejette ce
    dernier (vérifié, spec § 2.11).

    Une réponse vide ou non décodable est traitée comme ``OUT_OF_SCHEMA`` plutôt que de
    laisser remonter une exception : côté appelant, une question sans réponse
    exploitable et une question hors périmètre se traitent de la même façon.
    """
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(schema=schema_prompt)},
            {"role": "user", "content": question},
        ],
        response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
        max_completion_tokens=MAX_SQL_TOKENS,
    )
    try:
        data = json.loads(response.choices[0].message.content or "{}")
    except (json.JSONDecodeError, AttributeError, IndexError):
        data = {}

    status = data.get("status")
    if status not in ("SQL_GENERABLE", "AMBIGUOUS", "OUT_OF_SCHEMA"):
        status = "OUT_OF_SCHEMA"

    return Generation(
        status=status,
        sql=str(data.get("sql", "")),
        tables=tuple(str(t) for t in data.get("tables_referencees", [])),
        columns=tuple(str(c) for c in data.get("colonnes_referencees", [])),
        clarification=str(data.get("clarification", "")),
        reason=str(data.get("reason", "")),
    )
