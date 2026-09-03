"""Orchestration du Text-to-SQL — point d'entrée unique du chantier.

``SqlEngine`` est à ce chantier ce que ``SearchEngine`` est au RAG : la classe que les
scripts, l'interface et (plus tard) le serveur MCP instancient. Elle expose les quatre
opérations de la conception, et aucune ne prend le profil en paramètre — il est injecté
au constructeur. C'est ce qui rend le profil non falsifiable par un appelant de tool :
il n'y a pas de paramètre à falsifier.

Le pipeline de ``ask_database`` empile des barrières indépendantes, dans cet ordre
(conception § 2.2, spec § 3.3) :

    détection d'intention d'écriture (avant tout appel LLM)
    -> génération structurée + auto-déclaration des références
    -> vérification des références déclarées contre le schéma filtré
    -> validation structurale du SQL
    -> ajout du LIMIT
    -> EXPLAIN QUERY PLAN (authorizer actif, aucune donnée lue)
    -> exécution sous délai maximal (authorizer actif)

Chaque sortie, succès comme refus, écrit une entrée de trace.
"""

import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from gateway.settings import Settings
from sql.access import AccessRules
from sql.generate import generate_sql, looks_like_write
from sql.guard import (
    ValidationError,
    apply_limit,
    open_execution,
    open_introspection,
    run_query,
    validate_sql,
)
from sql.schema import SchemaResponse, covered_months, read_schema, schema_as_prompt
from sql.tools import CheckStockResult, OrderStatusResult, check_stock, order_status
from sql.trace import TraceRecorder

AskStatus = Literal["ok", "refused", "clarification"]

#: Message renvoyé au client pour un refus de périmètre. Fixe et écrit par nous : le
#: `reason` du modèle décrit ce qui manque malgré l'instruction contraire (mesuré,
#: spec § 2.11), ce qui renseignerait un profil sur des données qu'il ne doit pas
#: connaître. Le texte du modèle part dans la trace, utile au diagnostic (spec § 4.6).
OUT_OF_SCHEMA_MESSAGE = (
    "Cette question ne peut pas être traitée à partir des données accessibles."
)
FORBIDDEN_MESSAGE = "Cette demande n'est pas autorisée."
VALIDATION_MESSAGE = "La requête produite n'est pas une lecture valide."
TIMEOUT_MESSAGE = "La requête a été interrompue : temps d'exécution trop long."


@dataclass(frozen=True)
class AskDatabaseResult:
    """Résultat de ``ask_database``, quel que soit le chemin de sortie.

    ``sql_genere`` et ``sql_execute`` sont toujours portés quand du SQL a été produit —
    la trace en a besoin. Que le client final les voie est une décision de la couche
    MCP, pas de ce moteur (spec § 4.9).
    """

    status: AskStatus
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    row_count: int
    truncated: bool
    message: str
    code: str | None
    sql_genere: str
    sql_execute: str


class SqlEngine:
    """Moteur Text-to-SQL lié à un profil, avec ses règles d'accès et sa trace."""

    def __init__(
        self,
        profile: str,
        access_rules: AccessRules,
        trace: TraceRecorder,
        llm_client: Any,
        settings: Settings,
    ) -> None:
        self._profile = profile
        self._access_rules = access_rules
        self._trace = trace
        self._llm = llm_client
        self._settings = settings
        # Deux connexions aux rôles séparés : l'introspection a besoin de PRAGMA, que
        # l'authorizer de la connexion d'exécution refuse (vérifié, spec § 2.5).
        self._introspection = open_introspection(settings.sqlite_path)
        self._execution = open_execution(settings.sqlite_path, access_rules, profile)

    # --- tools ---------------------------------------------------------------

    def get_schema(self) -> SchemaResponse:
        """Schéma accessible à ce profil, lu à la source à chaque appel.

        Appelable par tous les profils : seul le contenu est filtré, jamais l'accès au
        tool lui-même (conception, qui fait autorité contre `tests/conftest.py` —
        spec § 8 point 3).
        """
        schema = read_schema(self._introspection, self._access_rules, self._profile)
        self._record("get_schema", "ok", None, question="", detail="")
        return schema

    def check_stock(self, ref: str) -> CheckStockResult:
        """Tool figé : stock d'une référence, sans LLM ni génération."""
        result = check_stock(self._execution, ref)
        self._record("check_stock", "ok", None, question=ref, detail="")
        return result

    def order_status(self, order_id: str) -> OrderStatusResult:
        """Tool figé : statut d'une commande, sans LLM ni génération."""
        result = order_status(self._execution, order_id)
        self._record("order_status", "ok", None, question=order_id, detail="")
        return result

    def ask_database(self, question: str) -> AskDatabaseResult:
        """Text-to-SQL complet : classification, génération, validation, exécution."""
        if looks_like_write(question):
            # Refus en amont : économise un appel LLM, et distingue dans la trace une
            # tentative de modification d'une simple question hors sujet (spec § 4.11).
            return self._refuse(question, FORBIDDEN_MESSAGE, "FORBIDDEN",
                                detail="intention d'écriture détectée dans la question")

        schema = read_schema(self._introspection, self._access_rules, self._profile)
        prompt = schema_as_prompt(schema, covered_months(self._introspection))
        generation = generate_sql(
            self._llm, self._settings.azure_model_text_generation, question, prompt
        )

        if generation.status == "AMBIGUOUS":
            return self._clarify(question, generation.clarification, generation.reason)
        if generation.status == "OUT_OF_SCHEMA":
            return self._refuse(question, OUT_OF_SCHEMA_MESSAGE, "OUT_OF_SCHEMA",
                                detail=generation.reason)

        unknown = self._unknown_references(generation, schema)
        if unknown:
            return self._refuse(
                question, OUT_OF_SCHEMA_MESSAGE, self._reference_code(unknown, schema),
                detail=f"références hors schéma filtré : {', '.join(sorted(unknown))}",
                sql_genere=generation.sql,
            )

        try:
            validate_sql(generation.sql)
        except ValidationError as error:
            return self._refuse(question, VALIDATION_MESSAGE, "VALIDATION",
                                detail=str(error), sql_genere=generation.sql)

        executable = apply_limit(generation.sql, self._settings.sql_default_limit)
        try:
            # EXPLAIN QUERY PLAN prépare la requête sans lire de données : dernier
            # filet contre une colonne interdite ou hallucinée (conception § 2.10).
            self._execution.execute(f"EXPLAIN QUERY PLAN {executable}").fetchall()
            columns, rows, truncated = run_query(
                self._execution, executable,
                self._settings.sql_timeout_s, self._settings.sql_default_limit,
            )
        except sqlite3.OperationalError as error:
            if "interrupted" in str(error):
                return self._refuse(question, TIMEOUT_MESSAGE, "TIMEOUT",
                                    detail=str(error), sql_genere=generation.sql,
                                    sql_execute=executable)
            return self._refuse(question, OUT_OF_SCHEMA_MESSAGE, "OUT_OF_SCHEMA",
                                detail=str(error), sql_genere=generation.sql,
                                sql_execute=executable)
        except sqlite3.DatabaseError as error:
            # « access to X.Y is prohibited » ou « not authorized » : l'authorizer a
            # rattrapé ce que les couches précédentes n'avaient pas vu.
            return self._refuse(question, FORBIDDEN_MESSAGE, "FORBIDDEN",
                                detail=str(error), sql_genere=generation.sql,
                                sql_execute=executable)

        self._record("ask_database", "ok", None, question=question, detail="",
                      sql_genere=generation.sql, sql_execute=executable)
        return AskDatabaseResult(
            status="ok", columns=tuple(columns), rows=tuple(rows), row_count=len(rows),
            truncated=truncated, message="", code=None,
            sql_genere=generation.sql, sql_execute=executable,
        )

    # --- internes ------------------------------------------------------------

    def _unknown_references(self, generation, schema: SchemaResponse) -> set[str]:
        """Références déclarées par le modèle qui ne sont pas dans le schéma filtré.

        Porte sur la déclaration, pas sur le SQL : comparer une liste est plus fiable
        que d'extraire les tables et colonnes d'une syntaxe avec alias, jointures et
        CTE (conception § 2.11).
        """
        tables = {t.name for t in schema.tables}
        columns = {f"{t.name}.{c.name}" for t in schema.tables for c in t.columns}
        inconnues = {t for t in generation.tables if t not in tables}
        inconnues |= {c for c in generation.columns if c not in columns}
        return inconnues

    def _reference_code(self, unknown: set[str], schema: SchemaResponse) -> str:
        """FORBIDDEN si la référence existe mais est filtrée, OUT_OF_SCHEMA sinon.

        Distinction utile en audit : « a demandé une colonne interdite » et « a inventé
        une colonne » n'ont pas la même signification.
        """
        reelles = self._access_rules.hidden_columns(self._profile)
        cachees = {f"{table}.{column}" for table, column in reelles}
        return "FORBIDDEN" if unknown & cachees else "OUT_OF_SCHEMA"

    def _refuse(
        self, question: str, message: str, code: str, detail: str,
        sql_genere: str = "", sql_execute: str = "",
    ) -> AskDatabaseResult:
        self._record("ask_database", "refused", code, question=question, detail=detail,
                      sql_genere=sql_genere, sql_execute=sql_execute)
        return AskDatabaseResult(
            status="refused", columns=(), rows=(), row_count=0, truncated=False,
            message=message, code=code, sql_genere=sql_genere, sql_execute=sql_execute,
        )

    def _clarify(self, question: str, clarification: str, reason: str) -> AskDatabaseResult:
        """La clarification du modèle est renvoyée telle quelle.

        Contrairement au message de refus (§ 4.6 de la spec), son intérêt est justement
        d'être spécifique à la question posée.
        """
        self._record("ask_database", "clarification", "AMBIGUOUS",
                      question=question, detail=reason)
        return AskDatabaseResult(
            status="clarification", columns=(), rows=(), row_count=0, truncated=False,
            message=clarification, code="AMBIGUOUS", sql_genere="", sql_execute="",
        )

    def _record(
        self, tool: str, statut: str, code: str | None, question: str, detail: str,
        sql_genere: str = "", sql_execute: str = "",
    ) -> None:
        """Écrit une entrée de trace. Appelé sur *chaque* chemin de sortie (E5)."""
        self._trace.record({
            "profil": self._profile,
            "tool": tool,
            "question": question,
            "statut": statut,
            "code": code,
            "detail": detail,
            "sql_genere": sql_genere,
            "sql_execute": sql_execute,
        })
