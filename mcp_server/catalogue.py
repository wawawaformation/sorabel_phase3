"""Catalogue des 8 tools MCP — noms, schémas d'entrée, descriptions orientées choix.

Descriptions et Server Instructions traduisent
``conception/commun/catalogue_tools_mcp.md`` et
``conception/3_MCP/questions_reponses_mcp.md`` § 3 : elles disent au host QUAND
utiliser chaque tool, jamais QUI est autorisé (ça, c'est ``access.py`` + le contrôle
RBAC, conception § 2.2). ``_meta["sorabel/roles"]`` est généré depuis la matrice,
jamais maintenu à la main (conception § 2.4).
"""

from __future__ import annotations

import mcp.types as types

from mcp_server.access import YamlAccessRules

SERVER_INSTRUCTIONS = """\
Sorabel exposes documentary RAG tools and structured-data SQL tools.

Routing rules:

1. For documentary questions, use the RAG tools.
2. For structured business data, use the SQL tools.
3. Prefer a specialized deterministic tool whenever one covers the request:
   - product stock -> check_stock
   - order status -> order_status
4. Use ask_database only when no specialized SQL tool covers the request.
5. For an exact documentary product reference, prefer:
   list_sources -> get_document.
6. Use answer_question for general documentary questions.
"""

TOOL_NAMES = (
    "answer_question", "search_docs", "get_document", "list_sources",
    "ask_database", "get_schema", "check_stock", "order_status",
)

_DESCRIPTIONS: dict[str, str] = {
    "answer_question": (
        "Question documentaire générale nécessitant le pipeline RAG complet "
        "(recherche hybride + décision de couverture + réponse rédigée et sourcée). "
        "Ne pas utiliser pour explorer sans générer (préférer search_docs) ni pour "
        "une référence produit exacte déjà connue (préférer list_sources -> get_document)."
    ),
    "search_docs": (
        "Recherche documentaire brute (dense + BM25 + fusion), sans décision de "
        "couverture ni réponse rédigée — exploration ou diagnostic du retrieval. "
        "Ne pas privilégier par défaut pour une réponse documentaire complète."
    ),
    "get_document": (
        "Récupère un document complet à partir d'un document_id déjà connu (par "
        "exemple via list_sources). Ne relance pas de recherche approximative."
    ),
    "list_sources": (
        "Liste ou identifie des sources par métadonnées (collection, type de "
        "document, référence produit). À privilégier pour résoudre une référence "
        "documentaire exacte (REF-xxxx) vers un document_id, avant get_document."
    ),
    "ask_database": (
        "Question métier en langage naturel sur les données structurées (produits, "
        "stocks, commandes, clients, ventes), quand aucun tool SQL spécialisé ne "
        "couvre directement le besoin. Ne pas utiliser pour le stock d'une référence "
        "précise (préférer check_stock) ni pour le statut d'une commande identifiée "
        "(préférer order_status)."
    ),
    "get_schema": (
        "Schéma SQL accessible au profil courant, lu à la source. Sert à connaître "
        "le périmètre réellement accessible avant d'écrire une question métier."
    ),
    "check_stock": (
        "Stock d'une référence produit précise, SQL figé et déterministe, sans LLM. "
        "À utiliser en priorité sur ask_database pour toute question de stock portant "
        "sur une référence connue."
    ),
    "order_status": (
        "Statut, date et montant d'une commande identifiée, SQL figé et "
        "déterministe, sans LLM. À utiliser en priorité sur ask_database pour toute "
        "question portant uniquement sur le statut d'une commande."
    ),
}

_INPUT_SCHEMAS: dict[str, dict] = {
    "answer_question": {
        "type": "object",
        "properties": {"question": {"type": "string"}, "top_k": {"type": "integer", "default": 5}},
        "required": ["question"],
    },
    "search_docs": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "default": 5},
            "include_score": {"type": "boolean", "default": False},
        },
        "required": ["query"],
    },
    "get_document": {
        "type": "object",
        "properties": {"document_id": {"type": "string"}},
        "required": ["document_id"],
    },
    "list_sources": {
        "type": "object",
        "properties": {
            "collection": {"type": "string"},
            "type_doc": {"type": "string"},
            "ref_produit": {"type": "string"},
            "include_versions": {"type": "boolean", "default": False},
        },
    },
    "ask_database": {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
    "get_schema": {"type": "object", "properties": {}},
    "check_stock": {
        "type": "object",
        "properties": {"ref": {"type": "string"}},
        "required": ["ref"],
    },
    "order_status": {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    },
}


def build_tools(access_rules: YamlAccessRules) -> list[types.Tool]:
    """Construit le catalogue, avec ``_meta["sorabel/roles"]`` dérivé de la matrice.

    ``meta=`` n'est pas le bon mot-clé (alias pydantic ``_meta``, vérifié en Task 4) :
    on passe ``**{"_meta": ...}`` pour atteindre le vrai champ.
    """
    tools = []
    for name in TOOL_NAMES:
        roles = sorted(
            profile for profile in ("commercial", "support")
            if name in access_rules.allowed_tools(profile)
        )
        tools.append(
            types.Tool(
                name=name,
                description=_DESCRIPTIONS[name],
                inputSchema=_INPUT_SCHEMAS[name],
                **{"_meta": {"sorabel/roles": roles}},  # type: ignore[arg-type]
            )
        )
    return tools
