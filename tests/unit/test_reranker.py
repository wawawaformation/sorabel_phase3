import httpx

from gateway.settings import Settings
from retrieval.reranker import AzureCohereReranker, RerankResult


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        azure_ai_endpoint="https://x.services.ai.azure.com/openai/v1",
        azure_ai_api_key="cle",
        azure_model_reranking="Cohere-rerank-v4.0-pro",
    )


def test_appel_http_et_lecture_de_la_reponse():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("api-key")
        captured["body"] = httpx.Request("POST", request.url, content=request.content).content
        # Format Cohere v1, vérifié contre le vrai endpoint (spec § 2.1).
        return httpx.Response(200, json={
            "results": [{"index": 1, "relevance_score": 0.8527},
                        {"index": 0, "relevance_score": 0.1719}],
            "meta": {"billed_units": {"search_units": 1}},
        })

    reranker = AzureCohereReranker(
        _settings(), http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    got = reranker.rerank("colis endommagé", ["notice led", "procedure colis"], top_n=2)

    assert got == [RerankResult(index=1, score=0.8527), RerankResult(index=0, score=0.1719)]
    # La route vit sous /models, pas sous /openai/v1.
    assert captured["url"].startswith("https://x.services.ai.azure.com/models/v1/rerank")
    assert captured["api_key"] == "cle"


def test_documents_vides_aucun_appel():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("aucun appel HTTP attendu pour une liste vide")

    reranker = AzureCohereReranker(
        _settings(), http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert reranker.rerank("q", [], top_n=5) == []
