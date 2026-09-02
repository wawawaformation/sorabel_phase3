"""Intégration : ingestion complète du corpus réel, Chroma éphémère, embedder factice.

Ni Docker ni appel réseau : la CI ne lance pas docker compose et n'a pas de clé Azure.
"""

from collections import Counter
from pathlib import Path

import chromadb
import pytest

from ingest.pipeline import ingest_corpus
from ingest.store import open_collection

CORPUS = Path("data/corpus")

ATTENDU_PAR_COLLECTION = {"fiches": 150, "notices": 80, "sav": 90, "notes": 80}


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0] for t in texts]


@pytest.fixture(scope="module")
def collection():
    client = chromadb.EphemeralClient()
    col = open_collection(client, "sorabel_corpus_test")
    written = ingest_corpus(CORPUS, col, FakeEmbedder())
    assert written == 400
    return col


def test_400_chunks(collection):
    assert collection.count() == 400


def test_repartition_par_collection(collection):
    for name, attendu in ATTENDU_PAR_COLLECTION.items():
        got = collection.get(where={"collection": name}, include=["metadatas"])
        assert len(got["ids"]) == attendu, name


def test_ref_produit_presente_sur_les_pdf_seulement(collection):
    all_meta = collection.get(include=["metadatas"])["metadatas"]
    avec = [m for m in all_meta if "ref_produit" in m]
    assert len(avec) == 230
    assert len(all_meta) - len(avec) == 170


def test_familles_et_groupes(collection):
    all_meta = collection.get(include=["metadatas"])["metadatas"]
    familles = Counter(m["family_id"] for m in all_meta)
    assert len(familles) == 350
    # 50 familles portent deux versions (30 fiches + 10 notices + 10 SAV).
    assert sum(1 for n in familles.values() if n == 2) == 50
    groupes = {m["diversification_group"] for m in all_meta}
    assert len({g for g in groupes if g.startswith("sav_")}) == 10
    assert len({g for g in groupes if g.startswith("note_")}) == 5


def test_reexecution_idempotente(collection):
    ingest_corpus(CORPUS, collection, FakeEmbedder())
    assert collection.count() == 400
