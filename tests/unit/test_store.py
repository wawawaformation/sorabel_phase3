from datetime import date

import chromadb

from ingest.chunk import Chunk
from ingest.store import chroma_metadata, open_collection, upsert_chunks


def _chunk(chunk_id: str, ref: str | None) -> Chunk:
    return Chunk(
        chunk_id=chunk_id, document_id=chunk_id.split("#")[0], chunk_index=0,
        family_id="fam", diversification_group="grp", content="contenu",
        title="Titre", type_doc="fiche_technique", collection="fiches",
        ref_produit=ref, version="2.1", date=date(2022, 11, 7), source="pdf",
    )


def test_metadonnees_scalaires_et_date_iso():
    meta = chroma_metadata(_chunk("a#0", "REF-1024"))
    assert meta["date"] == "2022-11-07"  # chaîne, pas objet date
    assert meta["chunk_index"] == 0
    assert meta["ref_produit"] == "REF-1024"
    assert all(isinstance(v, (str, int, float, bool)) for v in meta.values())


def test_reference_absente_omise_pas_none():
    # Chroma refuse une valeur None : la clé doit disparaître.
    meta = chroma_metadata(_chunk("b#0", None))
    assert "ref_produit" not in meta


def test_upsert_accepte_par_chroma_et_idempotent():
    client = chromadb.EphemeralClient()
    collection = open_collection(client, "sorabel_test")
    chunks = [_chunk("a#0", "REF-1024"), _chunk("b#0", None)]
    vectors = [[0.1, 0.2], [0.3, 0.4]]

    upsert_chunks(collection, chunks, vectors)
    assert collection.count() == 2

    # Ré-exécution : mise à jour, pas duplication.
    upsert_chunks(collection, chunks, vectors)
    assert collection.count() == 2
