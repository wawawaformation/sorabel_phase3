"""Descriptions métier des tables et colonnes, superposées à l'introspection SQLite.

Pourquoi ce fichier existe : la structure (tables, colonnes, types, relations) est lue
à la source par ``sql/schema.py`` via ``PRAGMA``, mais SQLite ne stocke aucun commentaire
— vérifié, le ``CREATE TABLE`` réel de ``sorabel.db`` n'en contient pas (spec § 2.2).
Les descriptions, indispensables pour que le modèle génère du SQL juste (conception
§ 1.2), sont donc une couche écrite à la main.

Le contenu est la transposition de ``docs/schema.sql``, rédigé et vérifié en conception.
Aucune génération par LLM : la sensibilité d'une colonne est une décision métier, pas une
inférence linguistique, et régénérer du contenu déjà vérifié n'ajouterait qu'un risque de
dérive (spec § 4.4).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnDoc:
    """Documentation d'une colonne : sa description métier et son vocabulaire fermé.

    ``values`` vaut ``None`` quand la colonne n'a pas d'ensemble de valeurs connu
    (un prix, une date, un libellé libre) — à distinguer d'un tuple vide, qui
    signifierait « aucune valeur possible ».
    """

    description: str
    values: tuple[str, ...] | None = None


TABLE_DOCS: dict[str, str] = {
    "produits": "Catalogue Sorabel : matériel électrique et outillage professionnel.",
    "stocks": "Quantités disponibles par entrepôt, une ligne par référence et entrepôt.",
    "clients": "Comptes professionnels clients.",
    "commandes": "Entêtes de commandes : client, date, statut, montant total.",
    "ventes": "Lignes de commandes, détail produit par produit.",
}

COLUMN_DOCS: dict[tuple[str, str], ColumnDoc] = {
    # --- produits ---
    ("produits", "ref"): ColumnDoc("Référence produit, format REF-NNNN (ex. REF-8842)."),
    ("produits", "nom"): ColumnDoc("Libellé commercial du produit."),
    ("produits", "categorie"): ColumnDoc(
        "Famille de produit : Protection électrique, Câblage, Outillage électroportatif, "
        "EPI, Éclairage, Mesure, Visserie, Distribution, Outillage à main."
    ),
    ("produits", "fabricant"): ColumnDoc("Marque du fournisseur (Voltane, Ferrix, Cablor…)."),
    ("produits", "unite"): ColumnDoc(
        "Unité de vente.", values=("pièce", "conditionnement")
    ),
    ("produits", "prix_vente_ht"): ColumnDoc("Prix public hors taxes, en euros."),
    ("produits", "prix_achat_ht"): ColumnDoc(
        "SENSIBLE — prix d'achat fournisseur hors taxes, en euros."
    ),
    ("produits", "marge_pct"): ColumnDoc(
        "SENSIBLE — marge exprimée en pourcentage du prix de vente."
    ),
    ("produits", "actif"): ColumnDoc(
        "1 = présent au catalogue, 0 = retiré du catalogue.", values=("0", "1")
    ),
    # --- stocks ---
    ("stocks", "id"): ColumnDoc("Identifiant technique de la ligne de stock."),
    ("stocks", "ref"): ColumnDoc("Référence produit concernée (vers produits.ref)."),
    ("stocks", "entrepot"): ColumnDoc(
        "Entrepôt de stockage.", values=("LILLE", "LYON", "NANTES")
    ),
    ("stocks", "quantite"): ColumnDoc(
        "Quantité en stock dans cet entrepôt. Le stock total d'une référence est la "
        "somme sur tous les entrepôts."
    ),
    ("stocks", "seuil_reappro"): ColumnDoc(
        "Seuil déclenchant le réapprovisionnement, propre à cet entrepôt."
    ),
    # --- clients ---
    ("clients", "id"): ColumnDoc("Identifiant interne du client, format CLI-NNNN."),
    ("clients", "raison_sociale"): ColumnDoc("Nom de l'entreprise cliente."),
    ("clients", "segment"): ColumnDoc(
        "Segment commercial du client.",
        values=("artisan", "PME", "grand compte", "collectivité"),
    ),
    ("clients", "ville"): ColumnDoc("Ville du client."),
    ("clients", "email"): ColumnDoc(
        "Contact principal — donnée personnelle, usage interne uniquement."
    ),
    # --- commandes ---
    ("commandes", "id"): ColumnDoc("Identifiant de commande, format CMD-AAAA-NNNN."),
    ("commandes", "client_id"): ColumnDoc("Client ayant passé la commande (vers clients.id)."),
    ("commandes", "date_commande"): ColumnDoc(
        "Date de la commande, au format ISO AAAA-MM-JJ. Seule date du modèle : les "
        "questions temporelles sur les ventes passent par cette colonne."
    ),
    ("commandes", "statut"): ColumnDoc(
        "Statut d'avancement de la commande.",
        values=("en_attente", "preparee", "expediee", "livree", "annulee"),
    ),
    ("commandes", "montant_ht"): ColumnDoc("Total hors taxes de la commande, en euros."),
    # --- ventes ---
    ("ventes", "id"): ColumnDoc("Identifiant technique de la ligne de vente."),
    ("ventes", "commande_id"): ColumnDoc("Commande à laquelle la ligne appartient."),
    ("ventes", "ref"): ColumnDoc("Référence produit vendue (vers produits.ref)."),
    ("ventes", "quantite"): ColumnDoc("Quantité vendue sur cette ligne."),
    ("ventes", "prix_unitaire_ht"): ColumnDoc(
        "Prix unitaire réellement facturé, remise déduite."
    ),
    ("ventes", "remise_pct"): ColumnDoc(
        "Remise accordée sur la ligne, en pourcentage.", values=("0", "5", "10")
    ),
    ("ventes", "marge_ht"): ColumnDoc(
        "SENSIBLE — marge réalisée sur cette ligne de vente, en euros."
    ),
}
