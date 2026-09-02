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


def test_search_docs_brut_sans_dedup_ni_refus():
    # Pas de reranker fourni : search_docs ne doit pas en avoir besoin, contrairement
    # à search() — c'est un mode brut, exploratoire (conception : "sans diversification").
    out = _engine(None).search_docs("colis endommage", top_k=5)
    assert out.query == "colis endommage"
    assert out.retrieval_count >= len(out.results)
    ids = [r.chunk_id for r in out.results]
    # Les deux versions de la même famille peuvent toutes les deux apparaître :
    # aucune déduplication en mode brut.
    assert "colis-v1.0#0" in ids or "colis-v2.0#0" in ids
    assert all(r.rrf_score is None for r in out.results)  # include_score=False par défaut


def test_search_docs_rang_et_score_expose():
    out = _engine(None).search_docs("colis endommage", top_k=3, include_score=True)
    assert [r.rank for r in out.results] == list(range(1, len(out.results) + 1))
    assert all(r.rrf_score is not None for r in out.results)
    scores = [r.rrf_score for r in out.results]
    assert scores == sorted(scores, reverse=True)


def test_list_sources_regroupe_par_famille_derniere_version_en_tete():
    out = _engine(None).list_sources(collection="sav")
    assert out.total_count == 1  # colis-v1.0 et colis-v2.0 : une seule famille "colis"
    source = out.sources[0]
    assert source.family_id == "colis"
    assert source.current_version.version == "2.0"  # la plus récente, pas la mieux classée
    assert source.current_version.chunk_count == 1
    assert source.older_versions == []  # include_versions=False par défaut


def test_list_sources_avec_versions_anterieures():
    out = _engine(None).list_sources(collection="sav", include_versions=True)
    older = out.sources[0].older_versions
    assert [v.version for v in older] == ["1.0"]


def test_list_sources_filtre_par_ref_produit():
    out = _engine(None).list_sources(ref_produit="REF-1459")
    assert out.total_count == 1
    assert out.sources[0].family_id == "led"
    assert out.filters_applied == {"ref_produit": "REF-1459"}


def test_list_sources_aucun_filtre_retourne_tout():
    out = _engine(None).list_sources()
    assert out.total_count == 2  # familles "colis" et "led"
    assert out.filters_applied == {}


def test_get_document_trouve():
    doc = _engine(FakeReranker({})).get_document("led")
    assert doc is not None
    assert doc.title == "Projecteur LED"
    assert doc.ref_produit == "REF-1459"


def test_get_document_absent():
    assert _engine(FakeReranker({})).get_document("inconnu") is None


def test_sans_rerank_pas_de_refus():
    # Sans reranker il n'existe pas de signal de refus fiable (spec § 4.3) :
    # le moteur retourne des résultats sans décider.
    out = _engine(None, rerank_enabled=False).search("quelle est la politique de teletravail ?")
    assert out.is_refusal is False
    assert out.hits
    assert all(h.rerank_score is None for h in out.hits)
