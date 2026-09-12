from __future__ import annotations

import json
import time
from collections.abc import Callable

import httpx
import pytest

from kip.adapters.embeddings.http import SERVED_MODEL_TTL_SECONDS, HttpEmbeddingAdapter
from kip.adapters.model_circuit import GuardedEmbedding, ModelCircuit
from kip.adapters.rerankers.http import HttpRerankerAdapter
from kip.errors import ConfigurationError, DependencyUnavailableError


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _serves(*model_ids: str) -> httpx.Response:
    """The `/models` answer of a runtime loaded with `model_ids`."""
    return httpx.Response(200, json={"data": [{"id": model_id} for model_id in model_ids]})


def test_embedding_adapter_uses_model_and_query_instruction() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            return _serves("served-embedder")
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
            lambda request: _serves("embedder")
            if request.url.path == "/models"
            else httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]})
        ),
    )

    with pytest.raises(DependencyUnavailableError, match="dimension"):
        adapter.embed_query("질문")


def test_reranker_returns_scores_in_relevance_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            return _serves("served-reranker")
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


def _recording_embedding(
    served: list[str],
    paths: list[str],
    *,
    model: str = "kip-qwen3-embedding-0.6b",
    clock: Callable[[], float] = time.monotonic,
) -> HttpEmbeddingAdapter:
    """An adapter whose runtime answers `/embeddings` for any model name."""

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/models":
            return _serves(*served)
        inputs = json.loads(request.content)["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [1.0, 2.0, 3.0]}
                    for index, _ in enumerate(inputs)
                ]
            },
        )

    return HttpEmbeddingAdapter(
        base_url="http://127.0.0.1:7997",
        model=model,
        revision="abc123",
        dimensions=3,
        client=_client(handler),
        clock=clock,
    )


def test_embedding_adapter_refuses_a_runtime_serving_another_model() -> None:
    # Infinity answers /embeddings with HTTP 200 for a model it does not
    # serve, so only the served list separates "right space" from "wrong".
    paths: list[str] = []
    adapter = _recording_embedding(["kip-arctic-embed-l-v2-ko"], paths)

    with pytest.raises(DependencyUnavailableError) as error:
        adapter.embed_query("참여율 변경")

    assert str(error.value) == (
        "embedding model service serves ['kip-arctic-embed-l-v2-ko'], not the configured "
        "'kip-qwen3-embedding-0.6b'; start the runtime with this model or fix models.embedding.model"
    )
    assert paths == ["/models"]  # the wrong space is never embedded in
    with pytest.raises(DependencyUnavailableError):
        adapter.embed_documents(["문서"])
    assert paths == ["/models", "/models"]  # a failed probe is not cached


def test_embedding_adapter_verifies_the_served_model_once_per_ttl() -> None:
    now = [1000.0]
    paths: list[str] = []
    adapter = _recording_embedding(["kip-qwen3-embedding-0.6b"], paths, clock=lambda: now[0])

    assert adapter.embed_query("질문") == [1.0, 2.0, 3.0]
    adapter.embed_documents(["문서", "문서2"])
    now[0] += SERVED_MODEL_TTL_SECONDS - 1
    adapter.embed_query("질문")

    assert paths == ["/models", "/embeddings", "/embeddings", "/embeddings"]


def test_a_runtime_restarted_with_another_model_is_caught_after_the_ttl() -> None:
    now = [0.0]
    served = ["kip-qwen3-embedding-0.6b"]
    paths: list[str] = []
    adapter = _recording_embedding(served, paths, clock=lambda: now[0])
    assert adapter.embed_query("질문")

    served[:] = ["kip-arctic-embed-l-v2-ko"]  # the operator restarted the runtime
    assert adapter.embed_query("질문")  # still inside the verified window
    now[0] += SERVED_MODEL_TTL_SECONDS

    with pytest.raises(DependencyUnavailableError, match="not the configured"):
        adapter.embed_query("질문")
    assert paths[-1] == "/models"


def test_an_unreachable_models_probe_is_reported_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            raise httpx.ConnectError("connection refused", request=request)
        raise AssertionError("must not embed before the runtime is verified")

    adapter = HttpEmbeddingAdapter(
        base_url="http://127.0.0.1:7997",
        model="embedder",
        revision="abc123",
        dimensions=3,
        client=_client(handler),
    )

    with pytest.raises(DependencyUnavailableError, match="embedding model service is unavailable"):
        adapter.embed_query("질문")


def test_a_served_model_mismatch_opens_the_model_circuit() -> None:
    now = [0.0]
    paths: list[str] = []
    guarded = GuardedEmbedding(
        _recording_embedding(["kip-arctic-embed-l-v2-ko"], paths),
        ModelCircuit(cooldown_seconds=30, clock=lambda: now[0]),
    )

    with pytest.raises(DependencyUnavailableError, match="not the configured"):
        guarded.embed_query("질문")
    with pytest.raises(DependencyUnavailableError, match="retrying in 30s"):
        guarded.embed_query("질문")

    assert paths == ["/models"]  # the open circuit stops re-probing the runtime


def test_reranker_refuses_a_runtime_serving_another_model() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/models":
            return _serves("kip-arctic-embed-l-v2-ko")
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]})

    adapter = HttpRerankerAdapter(
        base_url="http://127.0.0.1:7997",
        model="kip-bge-reranker-v2-m3",
        revision="def456",
        client=_client(handler),
    )

    with pytest.raises(DependencyUnavailableError) as error:
        adapter.rerank("승인 근거", ["후보"])

    assert str(error.value) == (
        "reranking model service serves ['kip-arctic-embed-l-v2-ko'], not the configured "
        "'kip-bge-reranker-v2-m3'; start the runtime with this model or fix models.reranker.model"
    )
    assert paths == ["/models"]


def test_the_served_model_probe_rides_the_calling_operations_budget() -> None:
    # A TTL boundary reached inside a multi-hour rebuild must not fail the
    # batch on the 10-second query budget.
    now = [0.0]
    budgets: list[tuple[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        budgets.append((request.url.path, request.extensions["timeout"]["read"]))
        if request.url.path == "/models":
            return _serves("embedder")
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]})

    adapter = HttpEmbeddingAdapter(
        base_url="http://127.0.0.1:7997",
        model="embedder",
        revision="abc123",
        dimensions=3,
        timeout_seconds=120.0,
        query_timeout_seconds=10.0,
        client=_client(handler),
        clock=lambda: now[0],
    )

    adapter.embed_documents(["문서"])
    now[0] += SERVED_MODEL_TTL_SECONDS
    adapter.embed_query("질문")

    assert budgets == [("/models", 120.0), ("/embeddings", 120.0), ("/models", 10.0), ("/embeddings", 10.0)]


def test_the_reranker_probe_keeps_a_short_budget_of_its_own() -> None:
    budgets: list[tuple[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        budgets.append((request.url.path, request.extensions["timeout"]["read"]))
        if request.url.path == "/models":
            return _serves("served-reranker")
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]})

    adapter = HttpRerankerAdapter(
        base_url="http://127.0.0.1:7997",
        model="served-reranker",
        revision="def456",
        timeout_seconds=120.0,
        client=_client(handler),
    )

    adapter.rerank("승인 근거", ["후보"])

    # A stalled runtime cannot burn the rerank budget twice.
    assert budgets == [("/models", 5.0), ("/rerank", 120.0)]


def test_only_one_thread_probes_at_a_ttl_boundary() -> None:
    import threading

    probes: list[float] = []
    started = threading.Barrier(4)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            probes.append(time.monotonic())
            time.sleep(0.05)
            return _serves("embedder")
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]})

    adapter = HttpEmbeddingAdapter(
        base_url="http://127.0.0.1:7997",
        model="embedder",
        revision="abc123",
        dimensions=3,
        client=_client(handler),
    )

    def embed() -> None:
        started.wait(5)
        adapter.embed_query("질문")

    workers = [threading.Thread(target=embed) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)

    assert len(probes) == 1
