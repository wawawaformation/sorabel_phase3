import chromadb

from gateway.settings import Settings
from retrieval.engine import SearchEngine
from retrieval.reranker import RerankResult


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class FakeReranker:
    """Score piloté par le test, indexé sur le contenu du document."""

    def __init__(self, scores_by_text: dict[str, float]) -> None:
        self.scores = scores_by_text

    def rerank(self, query, documents, top_n):
        scored = [
            RerankResult(index=i, score=self.scores.get(doc, 0.0))
            for i, doc in enumerate(documents)
        ]
        return sorted(scored, key=lambda r: r.score, reverse=True)[:top_n]


def _collection():
    client = chromadb.EphemeralClient()
    col = client.get_or_create_collection(name="engine_test", embedding_function=None)
    col.upsert(
        ids=["colis-v1.0#0", "colis-v2.0#0", "led#0"],
        embeddings=[[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]],
        documents=["procedure colis endommage", "procedure colis endommage v2", "notice led"],
        metadatas=[
            {"document_id": "colis-v1.0", "chunk_index": 0, "family_id": "colis",
             "diversification_group": "sav_casse", "title": "Colis endommagé",
             "type_doc": "procedure_sav", "collection": "sav", "version": "1.0",
             "date": "2025-01-01", "source": "html"},
            {"document_id": "colis-v2.0", "chunk_index": 0, "family_id": "colis",
             "diversification_group": "sav_casse", "title": "Colis endommagé",
             "type_doc": "procedure_sav", "collection": "sav", "version": "2.0",
             "date": "2026-04-05", "source": "html"},
            {"document_id": "led", "chunk_index": 0, "family_id": "led",
             "diversification_group": "notice_led", "title": "Projecteur LED",
             "type_doc": "notice", "collection": "notices", "version": "1.0",
             "date": "2024-03-23", "source": "pdf", "ref_produit": "REF-1459"},
        ],
    )
    return col


def _engine(reranker=None, **overrides):
    settings = Settings(_env_file=None, **overrides)
    return SearchEngine(_collection(), FakeEmbedder(), settings, reranker=reranker)


def test_question_couverte_retourne_des_resultats():
    reranker = FakeReranker({"procedure colis endommage v2": 0.85, "notice led": 0.10})
    out = _engine(reranker).search("que faire si un colis arrive endommage ?")
    assert out.is_refusal is False
    assert out.route == "hybrid"
    assert out.hits[0].chunk.title == "Colis endommagé"
    assert out.hits[0].rerank_score == 0.85


def test_hors_corpus_refuse_sous_le_seuil():
    reranker = FakeReranker({})  # tout à 0.0
    out = _engine(reranker).search("quelle est la politique de teletravail ?")
    assert out.is_refusal is True
    assert out.reason and "seuil" in out.reason
    assert out.hits == []


def test_versions_dedupliquees_derniere_gardee():
    reranker = FakeReranker({"procedure colis endommage v2": 0.85})
    out = _engine(reranker).search("colis endommage")
    ids = [h.chunk.chunk_id for h in out.hits]
    assert "colis-v1.0#0" not in ids  # v1.0 écartée par le filtrage de version
    assert "colis-v2.0#0" in ids


def test_reference_exacte_court_circuite_le_retrieval():
    out = _engine(FakeReranker({})).search("fiche REF-1459")
    assert out.route == "reference"
    assert out.is_refusal is False  # routing déterministe : jamais de refus
    assert out.hits[0].chunk.ref_produit == "REF-1459"
    assert out.hits[0].rerank_score is None


def test_sans_rerank_pas_de_refus():
    # Sans reranker il n'existe pas de signal de refus fiable (spec § 4.3) :
    # le moteur retourne des résultats sans décider.
    out = _engine(None, rerank_enabled=False).search("quelle est la politique de teletravail ?")
    assert out.is_refusal is False
    assert out.hits
    assert all(h.rerank_score is None for h in out.hits)
