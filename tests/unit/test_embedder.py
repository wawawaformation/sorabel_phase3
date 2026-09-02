from datetime import date

from ingest.chunk import Chunk
from ingest.embedder import embed_in_batches, embedding_text


class FakeEmbedder:
    """Embedder factice : aucun appel réseau, garde la trace des lots reçus."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[float(len(t)), 0.0] for t in texts]


def _chunk(ref: str | None) -> Chunk:
    return Chunk(
        chunk_id="c#0", document_id="c", chunk_index=0, family_id="c",
        diversification_group="c", content="contenu", title="Titre",
        type_doc="fiche_technique", collection="fiches", ref_produit=ref,
        version="1.0", date=date(2024, 1, 1), source="pdf",
    )


def test_texte_embedde_prefixe_titre_et_reference():
    # Décision de conception : le vecteur dense est calculé sur
    # title + ref_produit + content ; le content stocké reste brut.
    assert embedding_text(_chunk("REF-1024")) == "Titre REF-1024 contenu"


def test_reference_absente_omise_du_prefixe():
    assert embedding_text(_chunk(None)) == "Titre contenu"


def test_appels_groupes_par_lots():
    fake = FakeEmbedder()
    vectors = embed_in_batches(fake, [f"t{i}" for i in range(5)], batch_size=2)
    assert [len(b) for b in fake.batches] == [2, 2, 1]
    assert len(vectors) == 5
