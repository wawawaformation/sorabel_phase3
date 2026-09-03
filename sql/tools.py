"""Tools SQL figés : requêtes paramétrées, déterministes, sans LLM.

Pourquoi des tools figés à côté de ``ask_database`` : ces deux besoins sont stables et
connus d'avance, seule la valeur du paramètre change. Y répondre par du SQL écrit à la
main donne un déterminisme total, aucun coût de tokens, une latence minimale et une
surface de sécurité triviale — un LLM n'apporterait rien ici (conception § 4.4).

Les requêtes sont **paramétrées** (``?``), jamais construites par concaténation : la
référence ou l'identifiant reçus sont des valeurs, jamais du SQL.
"""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class WarehouseStock:
    entrepot: str
    quantite: int


@dataclass(frozen=True)
class CheckStockResult:
    """Stock d'une référence : total et détail par entrepôt.

    Le détail est inclus parce qu'une référence a plusieurs lignes dans ``stocks``, une
    par entrepôt (vérifié : REF-8842 est présente à LILLE, LYON et NANTES) — le total
    seul masquerait une information utile au métier.
    """

    ref: str
    found: bool
    total_quantity: int
    by_warehouse: tuple[WarehouseStock, ...]


@dataclass(frozen=True)
class OrderStatusResult:
    """Statut d'une commande. ``found=False`` n'est pas une erreur, juste une absence."""

    order_id: str
    found: bool
    status: str | None
    date_commande: str | None
    montant_ht: float | None


def check_stock(connection: sqlite3.Connection, ref: str) -> CheckStockResult:
    """Stock total et par entrepôt d'une référence produit.

    Une référence absente retourne ``found=False`` avec un total à zéro plutôt qu'une
    exception : l'appelant distingue ainsi « rien en stock » de « référence inconnue »
    par le drapeau, sans avoir à gérer une erreur.
    """
    rows = connection.execute(
        "SELECT entrepot, quantite FROM stocks WHERE ref = ? ORDER BY entrepot", (ref,)
    ).fetchall()
    by_warehouse = tuple(WarehouseStock(entrepot=str(r[0]), quantite=int(r[1])) for r in rows)
    return CheckStockResult(
        ref=ref,
        found=bool(by_warehouse),
        total_quantity=sum(w.quantite for w in by_warehouse),
        by_warehouse=by_warehouse,
    )


def order_status(connection: sqlite3.Connection, order_id: str) -> OrderStatusResult:
    """Statut, date et montant d'une commande.

    Commande introuvable -> ``found=False`` et champs à ``None``, pas d'exception
    (conception § 4.2 : ``CMD-2026-0042`` du jeu d'évaluation n'existe pas, et c'est
    une réponse légitime, pas une panne).
    """
    row = connection.execute(
        "SELECT statut, date_commande, montant_ht FROM commandes WHERE id = ?", (order_id,)
    ).fetchone()
    if row is None:
        return OrderStatusResult(
            order_id=order_id, found=False, status=None, date_commande=None, montant_ht=None
        )
    return OrderStatusResult(
        order_id=order_id,
        found=True,
        status=str(row[0]),
        date_commande=str(row[1]),
        montant_ht=float(row[2]),
    )
