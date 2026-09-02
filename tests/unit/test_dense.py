import chromadb

from retrieval.dense import dense_search


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
