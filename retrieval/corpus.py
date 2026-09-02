"""Chargement des chunks indexés depuis Chroma, à l'usage de tout le retrieval.

Chroma reste la seule source de vérité persistante — rien n'est reconstruit en
mémoire pour la durée de vie du process (spec § 4.6). ``load_chunks`` accepte des
filtres optionnels (``ids``, ``where``) pour ne rapatrier que le sous-ensemble dont
l'appelant a besoin à un instant donné : ``SearchEngine`` (retrieval/engine.py) n'en
garde jamais le résultat complet en mémoire — un lookup, même répété, repart d'un
appel Chroma ciblé plutôt que d'un cache local de tout le corpus. Seule exception,
inévitable : ``LexicalIndex`` (retrieval/lexical.py) doit voir tout le corpus une
fois pour construire son index BM25, mais ne conserve ensuite que cet index, pas les
``IndexedChunk`` eux-mêmes.
"""

from dataclasses import dataclass

from chromadb.api.models.Collection import Collection


@dataclass(frozen=True)
class IndexedChunk:
    """Un chunk du corpus, avec toutes ses métadonnées de recherche et d'affichage.

    Reconstruit à partir d'une entrée Chroma (id + document + metadata) — un chunk
    correspond ici à un document entier (1 document = 1 chunk sur ce corpus, spec
    § 2.5). Immuable (``frozen=True``) : ces objets sont partagés entre les index
    dense, lexical et les étages de dédup/diversification sans jamais être copiés.
    """

    chunk_id: str
    document_id: str
    content: str
    title: str
    type_doc: str
    collection: str
    version: str
    date: str  # ISO, tel que stocké dans Chroma
    source: str
    family_id: str
    diversification_group: str
    ref_produit: str | None


def load_chunks(
    collection: Collection,
    *,
    ids: list[str] | None = None,
    where: dict[str, str] | None = None,
) -> list[IndexedChunk]:
    """Récupère des chunks depuis Chroma et les reconstruit en ``IndexedChunk``.

    Sans filtre, rapatrie tout le corpus (utilisé par ``list_sources`` et par
    ``LexicalIndex`` pour construire son index BM25). Avec ``ids``, ne rapatrie que
    ces identifiants précis (``get_document``, métadonnées des candidats d'un étage
    du pipeline). Avec ``where``, laisse Chroma filtrer côté serveur sur une valeur
    de métadonnée (``_search_by_reference`` : ``where={"ref_produit": ...}``) plutôt
    que de charger tout le corpus pour filtrer côté Python. ``ids=[]`` court-circuite
    sans appel réseau : une liste de candidats vide en amont du pipeline ne doit pas
    déclencher une requête Chroma inutile.

    Un seul appel à ``collection.get`` (pas de pagination : même sans filtre, 400
    chunks tiennent dans une seule réponse). ``ref_produit`` est le seul champ
    optionnel — absent des métadonnées pour les collections sav/ et notes/ car Chroma
    rejette les valeurs ``None`` explicites, donc la clé est simplement omise à
    l'ingestion.
    """
    if ids == []:
        return []
    got = collection.get(
        ids=ids,
        where=where,  # type: ignore[arg-type]
        include=["documents", "metadatas"],  # type: ignore[list-item]
    )
    chunks: list[IndexedChunk] = []
    for chunk_id, content, meta in zip(
        got["ids"], got["documents"] or [], got["metadatas"] or [], strict=True
    ):
        chunks.append(
            IndexedChunk(
                chunk_id=chunk_id,
                document_id=str(meta["document_id"]),
                content=content,
                title=str(meta["title"]),
                type_doc=str(meta["type_doc"]),
                collection=str(meta["collection"]),
                version=str(meta["version"]),
                date=str(meta["date"]),
                source=str(meta["source"]),
                family_id=str(meta["family_id"]),
                diversification_group=str(meta["diversification_group"]),
                # clé absente pour sav/ et notes/ : Chroma refuse les valeurs None
                ref_produit=str(meta["ref_produit"]) if "ref_produit" in meta else None,
            )
        )
    return chunks


def by_chunk_id(chunks: list[IndexedChunk]) -> dict[str, IndexedChunk]:
    """Index les chunks par chunk_id, pour un lookup O(1) depuis un identifiant.

    Le pipeline (dense, BM25, RRF, dédup) ne manipule que des listes de chunk_id ;
    ce dictionnaire est ce qui permet de retrouver le chunk complet (titre, contenu,
    métadonnées) au moment de construire la réponse finale.
    """
    return {c.chunk_id: c for c in chunks}
