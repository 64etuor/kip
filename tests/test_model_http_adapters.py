from __future__ import annotations

import json

import httpx
import pytest

from kip.adapters.embeddings.http import HttpEmbeddingAdapter
from kip.adapters.rerankers.http import HttpRerankerAdapter
from kip.errors import ConfigurationError, DependencyUnavailableError


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_embedding_adapter_uses_model_and_query_instruction() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [float(index), 1.0, 2.0]}
                    for index, _ in enumerate(payload["input"])
                ]
            },
        )

    adapter = HttpEmbeddingAdapter(
        base_url="http://127.0.0.1:7997",
        model="served-embedder",
        revision="abc123",
        dimensions=3,
        query_instruction="Retrieve Korean evidence: ",
        client=_client(handler),
    )

    assert adapter.embed_query("참여율 변경") == [0.0, 1.0, 2.0]
    assert adapter.embed_documents(["첫 문서", "둘째 문서"]) == [
        [0.0, 1.0, 2.0],
        [1.0, 1.0, 2.0],
    ]
    assert requests[0] == {
        "model": "served-embedder",
        "input": ["Retrieve Korean evidence: 참여율 변경"],
    }
    assert requests[1]["input"] == ["첫 문서", "둘째 문서"]


def test_embedding_adapter_rejects_wrong_dimensions() -> None:
    adapter = HttpEmbeddingAdapter(
        base_url="http://localhost:7997",
        model="embedder",
        revision="abc123",
        dimensions=3,
        client=_client(
            lambda request: httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]}
            )
        ),
    )

    with pytest.raises(DependencyUnavailableError, match="dimension"):
        adapter.embed_query("질문")


def test_reranker_returns_scores_in_relevance_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "served-reranker"
        assert payload["query"] == "승인 근거"
        assert payload["documents"] == ["낮음", "높음"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": -1.0},
                    {"index": 1, "relevance_score": 4.0},
                ]
            },
        )

    adapter = HttpRerankerAdapter(
        base_url="http://127.0.0.1:7997",
        model="served-reranker",
        revision="def456",
        client=_client(handler),
    )

    scores = adapter.rerank("승인 근거", ["낮음", "높음"])

    assert [(score.index, score.score) for score in scores] == [(1, 4.0), (0, -1.0)]


def test_model_adapters_wrap_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unavailable", request=request)

    adapter = HttpEmbeddingAdapter(
        base_url="http://127.0.0.1:7997",
        model="embedder",
        revision="abc123",
        dimensions=3,
        client=_client(handler),
    )

    with pytest.raises(DependencyUnavailableError, match="unavailable"):
        adapter.embed_query("질문")


def test_remote_model_url_is_rejected_when_egress_is_disabled() -> None:
    with pytest.raises(ConfigurationError, match="loopback"):
        HttpEmbeddingAdapter(
            base_url="https://models.example.com",
            model="embedder",
            revision="abc123",
            dimensions=3,
            allow_remote_egress=False,
        )


def test_model_clients_do_not_inherit_ambient_proxy(monkeypatch) -> None:
    monkeypatch.setenv("ALL_PROXY", "socks5h://127.0.0.1:1")

    embedding = HttpEmbeddingAdapter(
        base_url="http://127.0.0.1:7997",
        model="embedder",
        revision="abc123",
        dimensions=3,
    )
    reranker = HttpRerankerAdapter(
        base_url="http://127.0.0.1:7997",
        model="reranker",
        revision="abc123",
    )

    assert embedding.client is not None
    assert reranker.client is not None
