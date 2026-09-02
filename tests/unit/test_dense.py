import chromadb

from retrieval.dense import dense_search, dense_search_with_distances


class FakeEmbedder:
    """Renvoie un vecteur fixe : c'est Chroma qui fait le classement, pas l'embedder."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def _collection():
    client = chromadb.EphemeralClient()
    col = client.get_or_create_collection(name="dense_test", embedding_function=None)
    col.upsert(
        ids=["proche#0", "loin#0"],
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
        documents=["proche", "loin"],
        metadatas=[{"collection": "fiches"}, {"collection": "fiches"}],
    )
    return col


def test_classement_par_proximite():
    ids = dense_search(_collection(), FakeEmbedder(), "peu importe", limit=2)
    assert ids == ["proche#0", "loin#0"]


def test_limite_respectee():
    assert dense_search(_collection(), FakeEmbedder(), "q", limit=1) == ["proche#0"]


def test_avec_distances_plus_bas_est_plus_proche():
    # Chroma en L2 (défaut, pas d'espace configuré à la création) : distance 0 pour le
    # point identique au vecteur requête, distance > 0 pour l'éloigné.
    results = dense_search_with_distances(_collection(), FakeEmbedder(), "peu importe", limit=2)
    assert [chunk_id for chunk_id, _ in results] == ["proche#0", "loin#0"]
    assert results[0][1] < results[1][1]
    assert results[0][1] == 0.0  # même vecteur que la requête


def test_dense_search_reste_coherent_avec_la_version_scoree():
    plain = dense_search(_collection(), FakeEmbedder(), "q", limit=2)
    scored = dense_search_with_distances(_collection(), FakeEmbedder(), "q", limit=2)
    assert plain == [chunk_id for chunk_id, _ in scored]
