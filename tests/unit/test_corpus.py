import chromadb

from retrieval.corpus import IndexedChunk, by_chunk_id, load_chunks
from retrieval.tokenize import tokenize


def test_tokenisation_replie_accents_et_decoupe():
    assert tokenize("Disjoncteur triphasé 63 A — REF-8842, 230/400 V") == [
        "disjoncteur", "triphase", "63", "a", "ref", "8842", "230", "400", "v",
    ]


def test_chargement_depuis_chroma():
    client = chromadb.EphemeralClient()
    col = client.get_or_create_collection(name="corpus_test", embedding_function=None)
    col.upsert(
        ids=["a#0", "b#0"],
        embeddings=[[0.1, 0.2], [0.3, 0.4]],
        documents=["contenu a", "contenu b"],
        metadatas=[
            {"document_id": "a", "chunk_index": 0, "family_id": "fam-a",
             "diversification_group": "grp-a", "title": "Titre A",
             "type_doc": "fiche_technique", "collection": "fiches", "version": "2.1",
             "date": "2022-11-07", "source": "pdf", "ref_produit": "REF-1024"},
            # pas de ref_produit : cas normal de sav/ et notes/
            {"document_id": "b", "chunk_index": 0, "family_id": "fam-b",
             "diversification_group": "grp-b", "title": "Titre B",
             "type_doc": "procedure_sav", "collection": "sav", "version": "1.0",
             "date": "2026-04-05", "source": "html"},
        ],
    )
    chunks = load_chunks(col)
    assert len(chunks) == 2
    index = by_chunk_id(chunks)
    assert index["a#0"].ref_produit == "REF-1024"
    assert index["b#0"].ref_produit is None  # clé absente → None
    assert index["a#0"].content == "contenu a"
    assert isinstance(index["a#0"], IndexedChunk)
