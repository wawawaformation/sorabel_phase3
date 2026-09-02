from pathlib import Path

from ingest.settings import Settings


def test_valeurs_par_defaut():
    s = Settings(_env_file=None)
    assert s.corpus_dir == Path("data/corpus")
    assert s.chroma_collection == "sorabel_corpus"
    assert s.azure_model_text_embedding_small == "text-embedding-3-small"


def test_lecture_depuis_environnement(monkeypatch):
    monkeypatch.setenv("AZURE_AI_API_KEY", "cle-de-test")
    monkeypatch.setenv("CHROMA_URL", "http://ailleurs:9000")
    s = Settings(_env_file=None)
    assert s.azure_ai_api_key == "cle-de-test"
    assert s.chroma_url == "http://ailleurs:9000"


def test_variables_inconnues_ignorees(monkeypatch):
    # .env contient SORABEL_PROFILE, GATEWAY_JOURNAL, AZURE_MODEL_RERANKING…
    # qui ne sont pas des champs de Settings : ils ne doivent pas faire échouer.
    monkeypatch.setenv("SORABEL_PROFILE", "support")
    monkeypatch.setenv("AZURE_MODEL_RERANKING", "Cohere-rerank-v4.0-pro")
    Settings(_env_file=None)
