"""Intégration : pipeline complet sur le corpus réel, sans réseau.

Chroma éphémère peuplé par l'ingestion, embedder et reranker factices.
"""

from pathlib import Path

import chromadb
import pytest

from gateway.chroma import open_collection
from gateway.settings import Settings
from ingest.pipeline import ingest_corpus
from retrieval.engine import SearchEngine
from retrieval.reranker import RerankResult
from retrieval.tokenize import tokenize

CORPUS = Path("data/corpus")


class FakeEmbedder:
    """Embedding jouet mais discriminant : sac de mots projeté sur 16 dimensions."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * 16
            for token in tokenize(text):
                vec[hash(token) % 16] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


class LexicalOverlapReranker:
    """Score = recouvrement de jetons question/document, dans [0, 1]."""

    def rerank(self, query, documents, top_n):
        q = set(tokenize(query))
        scored = []
        for i, doc in enumerate(documents):
            d = set(tokenize(doc))
            scored.append(RerankResult(index=i, score=len(q & d) / (len(q) or 1)))
        return sorted(scored, key=lambda r: r.score, reverse=True)[:top_n]


@pytest.fixture(scope="module")
def collection():
    client = chromadb.EphemeralClient()
    col = open_collection(client, "retrieval_integration_test")
    assert ingest_corpus(CORPUS, col, FakeEmbedder()) == 400
    return col


@pytest.fixture(scope="module")
def engine(collection):
    settings = Settings(_env_file=None, refusal_threshold=0.0)
    return SearchEngine(
        collection, FakeEmbedder(), settings, reranker=LexicalOverlapReranker()
    )


def test_reference_exacte_trouve_la_fiche(engine):
    out = engine.search("REF-8842")
    assert out.route == "reference"
    assert out.hits
    assert out.hits[0].chunk.ref_produit == "REF-8842"
    # La dernière version en tête : le corpus a REF-8842 en v1.0 et v2.1.
    assert out.hits[0].chunk.version == "2.1"


def test_question_couverte_passe_par_l_hybride(engine):
    out = engine.search("que faire si un colis arrive endommagé ?")
    assert out.route == "hybrid"
    assert out.is_refusal is False
    assert out.hits
    assert {"dense", "lexical", "fused", "reranked"} <= set(out.stages)


def test_aucun_doublon_de_famille_ni_de_groupe(engine):
    out = engine.search("procédure de retour d'un produit défectueux")
    familles = [h.chunk.family_id for h in out.hits]
    groupes = [h.chunk.diversification_group for h in out.hits]
    assert len(familles) == len(set(familles))
    assert len(groupes) == len(set(groupes))


def test_seuil_haut_declenche_le_refus(collection):
    haut = Settings(_env_file=None, refusal_threshold=0.99)
    strict = SearchEngine(
        collection, FakeEmbedder(), haut, reranker=LexicalOverlapReranker()
    )
    out = strict.search("quelle est la politique de télétravail chez Sorabel ?")
    assert out.is_refusal is True
    assert out.hits == []
