from pathlib import Path

from gateway.settings import Settings


def test_valeurs_par_defaut():
    s = Settings(_env_file=None)
    assert s.corpus_dir == Path("data/corpus")
    assert s.chroma_collection == "sorabel_corpus"
    assert s.azure_model_text_embedding_small == "text-embedding-3-small"
    # nouveaux champs du retrieval
    assert s.rerank_enabled is True
    assert s.refusal_threshold == 0.65
    assert (s.dense_candidates, s.lexical_candidates) == (30, 30)
    assert (s.fusion_candidates, s.rerank_candidates, s.top_k) == (20, 10, 5)
    assert s.rrf_k == 60


def test_base_url_des_modeles_non_openai():
    # Le rerank vit sous /models, pas sous /openai/v1 : la propriété retire ce suffixe.
    s = Settings(_env_file=None, azure_ai_endpoint="https://x.services.ai.azure.com/openai/v1")
    assert s.azure_models_base_url == "https://x.services.ai.azure.com"


def test_lecture_depuis_environnement(monkeypatch):
    monkeypatch.setenv("AZURE_AI_API_KEY", "cle-de-test")
    monkeypatch.setenv("RERANK_ENABLED", "false")
    s = Settings(_env_file=None)
    assert s.azure_ai_api_key == "cle-de-test"
    assert s.rerank_enabled is False


def test_variables_inconnues_ignorees(monkeypatch):
    monkeypatch.setenv("SORABEL_PROFILE", "support")
    monkeypatch.setenv("GATEWAY_JOURNAL", "logs/journal.jsonl")
    Settings(_env_file=None)
