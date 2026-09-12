"""Default-on semantic search: maintenance, reviewed activation and fallbacks (ADR-065)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kip.adapters.embeddings.http import require_allowed_model_url
from kip.adapters.model_circuit import GuardedEmbedding, ModelCircuit
from kip.application.projection_maintenance import after_sync
from kip.application.semantic import EMBEDDING_DEFAULTS
from kip.container import build_container
from kip.domain.models import SearchRequest
from kip.errors import ConfigurationError, DependencyUnavailableError
from kip.ports.reranker import RerankScore

REVIEWED_INSTRUCTION = "Retrieve relevant Korean evidence for this query: "


class ReviewedEmbedding:
    """Presents the release-reviewed identity with deterministic vectors."""

    name = "http"
    provider = "infinity"
    model = "kip-qwen3-embedding-0.6b"
    revision = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    dimensions = 1024
    normalized = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def _vector(self, text: str) -> list[float]:
        values = [0.0] * self.dimensions
        values[0 if "승인" in text else 1] = 1.0
        return values

    def embed_query(self, text: str) -> list[float]:
        if self.fail:
            raise DependencyUnavailableError("model runtime down")
        return self._vector(text)

    def embed_documents(self, texts) -> list[list[float]]:
        if self.fail:
            raise DependencyUnavailableError("model runtime down")
        return [self._vector(text) for text in texts]


class CustomEmbedding(ReviewedEmbedding):
    model = "operator-custom-embedding"


class FailingReranker:
    name = "http"
    provider = "infinity"
    model = "kip-bge-reranker-v2-m3"
    revision = "fixture"

    def rerank(self, query: str, documents) -> list[RerankScore]:
        raise DependencyUnavailableError("reranker down")


def _semantic_container(test_container, tmp_path: Path, embedding, reranker=None):
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    (source_root / "날씨.txt").write_text("오늘의 날씨 안내입니다.", encoding="utf-8")
    raw = test_container.settings.raw
    raw["search"].update({"semantic_enabled": True, "default_mode": "reranked"})
    raw.setdefault("models", {})["embedding"] = {
        "max_document_chars": EMBEDDING_DEFAULTS["max_document_chars"],
        "query_instruction": REVIEWED_INSTRUCTION,
    }
    return build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=embedding,
        reranker=reranker,
    )


def test_sync_embeds_and_activates_the_release_reviewed_space(test_container, tmp_path: Path) -> None:
    container = _semantic_container(test_container, tmp_path, ReviewedEmbedding())
    context = container.application.operations.request_context()

    summary = after_sync(
        container.application.retrieval,
        context,
        container.application.ingestion.sync_filesystem(context, "fixture"),
    )

    update = summary.semantic_projection
    assert update is not None
    assert (update.status, update.activated, update.active) == ("updated", True, True)
    assert update.indexed_units == update.content_units > 0
    assert container.application.operations.capabilities(context).semantic_search is True
    hits = container.application.retrieval.search(context, SearchRequest(query="승인"))
    assert hits and "vector" in hits[0].metadata["retrieval_channels"]

    again = after_sync(
        container.application.retrieval,
        context,
        container.application.ingestion.sync_filesystem(context, "fixture"),
    )
    assert again.semantic_projection is not None
    assert (again.semantic_projection.status, again.semantic_projection.activated) == ("current", False)


def test_an_unreviewed_identity_is_completed_but_left_for_explicit_activation(
    test_container, tmp_path: Path
) -> None:
    container = _semantic_container(test_container, tmp_path, CustomEmbedding())
    context = container.application.operations.request_context()

    summary = after_sync(
        container.application.retrieval,
        context,
        container.application.ingestion.sync_filesystem(context, "fixture"),
    )

    update = summary.semantic_projection
    assert update is not None and update.active is False and update.activated is False
    assert update.reason and "kip projection activate" in update.reason
    assert any("kip projection activate" in warning for warning in summary.warnings)
    fallback = container.application.retrieval.search(context, SearchRequest(query="승인"))
    assert fallback and fallback[0].metadata["semantic_degraded"] is True


def test_model_runtime_down_keeps_sync_and_search_working(test_container, tmp_path: Path) -> None:
    container = _semantic_container(test_container, tmp_path, ReviewedEmbedding(fail=True))
    context = container.application.operations.request_context()

    summary = after_sync(
        container.application.retrieval,
        context,
        container.application.ingestion.sync_filesystem(context, "fixture"),
    )

    assert summary.inserted == 2
    update = summary.semantic_projection
    assert update is not None and update.status == "unavailable"
    assert update.reason and "semantic-server.sh start" in update.reason
    hits = container.application.retrieval.search(context, SearchRequest(query="승인"))
    assert hits and hits[0].metadata["semantic_degraded"] is True
    assert container.application.retrieval.result_warnings(context, hits) == ["semantic_degraded"]


def test_reranker_failure_keeps_the_fused_vector_ranking(test_container, tmp_path: Path) -> None:
    container = _semantic_container(test_container, tmp_path, ReviewedEmbedding(), reranker=FailingReranker())
    context = container.application.operations.request_context()
    after_sync(
        container.application.retrieval,
        context,
        container.application.ingestion.sync_filesystem(context, "fixture"),
    )

    hits = container.application.retrieval.search(context, SearchRequest(query="승인"))

    assert hits and hits[0].metadata["rerank_degraded"] is True
    assert "vector" in hits[0].metadata["retrieval_channels"]
    assert "semantic_degraded" not in hits[0].metadata
    assert container.application.retrieval.result_warnings(context, hits) == ["rerank_degraded"]
    with pytest.raises(DependencyUnavailableError):
        container.application.retrieval.search(context, SearchRequest(query="승인"), mode="reranked")


def test_model_circuit_fails_fast_during_cooldown_and_probes_again() -> None:
    now = [100.0]
    calls: list[str] = []

    class Flaky(ReviewedEmbedding):
        def embed_query(self, text: str) -> list[float]:
            calls.append(text)
            if self.fail:
                raise DependencyUnavailableError("connection refused")
            return self._vector(text)

    delegate = Flaky(fail=True)
    guarded = GuardedEmbedding(delegate, ModelCircuit(cooldown_seconds=30, clock=lambda: now[0]))

    with pytest.raises(DependencyUnavailableError, match="connection refused"):
        guarded.embed_query("a")
    with pytest.raises(DependencyUnavailableError, match="retrying in 30s"):
        guarded.embed_query("b")
    assert calls == ["a"]  # the open circuit never reached the runtime

    now[0] += 31
    delegate.fail = False
    assert guarded.embed_query("c")[0] == 0.0
    assert calls == ["a", "c"]
    assert (guarded.model, guarded.dimensions) == (delegate.model, delegate.dimensions)


def test_model_urls_accept_only_loopback_or_named_internal_services() -> None:
    assert require_allowed_model_url("http://127.0.0.1:7997/", False) == "http://127.0.0.1:7997"
    assert require_allowed_model_url("http://models:7997", False, ("models",)) == "http://models:7997"
    with pytest.raises(ConfigurationError, match="model_service_hosts"):
        require_allowed_model_url("http://models:7997", False)
    with pytest.raises(ConfigurationError):
        require_allowed_model_url("http://models.example.com:7997", False, ("models",))
    assert require_allowed_model_url("https://api.example.com", True) == "https://api.example.com"


def test_lexical_match_query_drops_corpus_common_terms_but_keeps_enough_to_match() -> None:
    from kip.adapters.repository.postgres.database import _websearch_or_query

    lexemes = "부적합품 부적 적합 절차 에서 승인권자"
    pruned = _websearch_or_query(lexemes, exclude=frozenset({"절차", "에서"}))
    assert '"절차"' not in pruned and '"에서"' not in pruned
    assert '"부적합품"' in pruned and '"승인권자"' in pruned

    # Only common terms: keep the longest ones so a short query still matches.
    fallback = _websearch_or_query("절차 에서 절차서", exclude=frozenset({"절차", "에서", "절차서"}), min_terms=2)
    assert fallback == '"절차서" OR "절차"'
    assert _websearch_or_query(lexemes) == _websearch_or_query(lexemes, exclude=frozenset())


def test_embedding_batches_are_bounded_by_characters_and_count() -> None:
    from kip.application.semantic import _bounded_batches
    from kip.domain.models import EmbeddableUnit

    def unit(index: int, chars: int) -> EmbeddableUnit:
        return EmbeddableUnit(unit_id=f"u{index}", title="", body_normalized="가" * chars, source_hash="h")

    units = [unit(0, 10), unit(1, 10), unit(2, 10), unit(3, 9000), unit(4, 9000), unit(5, 20000)]
    batches = _bounded_batches(units, max_units=4, max_chars=16000, max_document_chars=12000)

    assert [[item.unit_id for item in batch] for batch in batches] == [
        ["u0", "u1", "u2", "u3"],
        ["u4"],
        ["u5"],  # a unit longer than the budget still goes alone (capped at 12000)
    ]


def test_maintenance_errors_never_fail_a_committed_sync(test_container, tmp_path: Path, monkeypatch) -> None:
    container = _semantic_container(test_container, tmp_path, ReviewedEmbedding())
    context = container.application.operations.request_context()
    summary = container.application.ingestion.sync_filesystem(context, "fixture")

    def explode(*_args, **_kwargs):
        raise RuntimeError("canceling statement due to statement timeout")

    monkeypatch.setattr(container.application.retrieval, "maintain_semantic_projection", explode)
    result = after_sync(container.application.retrieval, context, summary)

    assert result.inserted == summary.inserted
    assert result.semantic_projection is not None and result.semantic_projection.status == "unavailable"
    assert any("statement timeout" in warning and "sync itself succeeded" in warning for warning in result.warnings)


def test_degraded_default_search_keeps_the_lexical_rerank_and_context_reports_it(
    test_container, tmp_path: Path
) -> None:
    from kip.domain.models import ContextRequest

    test_container.settings.raw["search"]["lexical_rerank_enabled"] = True
    test_container.settings.raw.setdefault("models", {})["reranker"] = {"enabled": True, "backend": "bm25"}
    container = _semantic_container(test_container, tmp_path, ReviewedEmbedding(fail=True))
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")

    hits = container.application.retrieval.search(context, SearchRequest(query="승인"))
    bundle = container.application.retrieval.context_bundle(context, ContextRequest(query="승인"))

    assert hits[0].metadata["semantic_degraded"] is True and "rerank_rank" in hits[0].metadata
    assert bundle.items and container.application.retrieval.result_warnings(context, bundle.items) == ["semantic_degraded"]


def test_auto_activation_never_replaces_an_explicitly_activated_custom_space(
    test_container, tmp_path: Path
) -> None:
    custom = _semantic_container(test_container, tmp_path, CustomEmbedding())
    context = custom.application.operations.request_context()
    custom.application.ingestion.sync_filesystem(context, "fixture")
    custom.application.retrieval.rebuild_semantic_projection(context)
    custom_space = custom.application.retrieval.activate_semantic_projection(context)

    reviewed = build_container(
        custom.settings, repository=test_container.repository, embedding=ReviewedEmbedding()
    )
    update = reviewed.application.retrieval.maintain_semantic_projection(context)

    assert update.status == "updated" and update.activated is False
    assert update.reason and "activated explicitly" in update.reason
    assert test_container.repository.retrieval.active_embedding_space(context).id == custom_space.id


def test_rebuild_pages_through_every_pending_unit(test_container, tmp_path: Path) -> None:
    container = _semantic_container(test_container, tmp_path, ReviewedEmbedding())
    for index in range(5):
        (tmp_path / "source" / f"추가{index}.txt").write_text(f"승인 근거 {index}", encoding="utf-8")
    container.settings.raw["models"]["embedding"]["page_size"] = 2
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")

    update = container.application.retrieval.maintain_semantic_projection(context)

    assert update.content_units == 7 and update.indexed_units == 7 and update.newly_indexed_units == 7
    assert update.activated is True


def test_model_circuit_lets_exactly_one_probe_through_after_the_cooldown() -> None:
    import threading

    now = [0.0]
    circuit = ModelCircuit(cooldown_seconds=10, clock=lambda: now[0])
    with pytest.raises(DependencyUnavailableError):
        circuit.call("embedding model", lambda: (_ for _ in ()).throw(DependencyUnavailableError("down")))
    now[0] = 11
    entered, release = threading.Event(), threading.Event()

    def slow_probe() -> str:
        entered.set()
        release.wait(5)
        return "ok"

    results: list[object] = []
    worker = threading.Thread(target=lambda: results.append(circuit.call("embedding model", slow_probe)))
    worker.start()
    entered.wait(5)
    with pytest.raises(DependencyUnavailableError, match="retrying"):
        circuit.call("embedding model", lambda: "second caller")  # fails fast while probing
    release.set()
    worker.join(5)
    assert results == ["ok"]
    assert circuit.call("embedding model", lambda: "closed") == "closed"


def test_model_service_hosts_must_be_bare_service_names() -> None:
    for entry in ("models.example.com", "10.0.0.5", "::1", "", "  "):
        with pytest.raises(ConfigurationError, match="bare service names"):
            require_allowed_model_url("http://models:7997", False, (entry,))


def test_numeric_host_names_cannot_reopen_remote_egress() -> None:
    for entry in ("134744072", "0x08080808", "8"):
        with pytest.raises(ConfigurationError, match="bare service names"):
            require_allowed_model_url("http://models:7997", False, (entry,))
    assert require_allowed_model_url("http://kip-models_1:7997", False, ("kip-models_1",))


def test_a_probe_that_raises_something_else_does_not_wedge_the_circuit() -> None:
    now = [0.0]
    circuit = ModelCircuit(cooldown_seconds=5, clock=lambda: now[0])
    with pytest.raises(DependencyUnavailableError):
        circuit.call("reranker model", lambda: (_ for _ in ()).throw(DependencyUnavailableError("down")))
    now[0] = 6
    with pytest.raises(OverflowError):
        circuit.call("reranker model", lambda: (_ for _ in ()).throw(OverflowError("bad reply")))
    assert circuit.call("reranker model", lambda: "probe again") == "probe again"


def test_a_semantic_config_without_default_mode_uses_the_shipped_hybrid_default(
    test_container, tmp_path: Path
) -> None:
    container = _semantic_container(test_container, tmp_path, ReviewedEmbedding(), reranker=FailingReranker())
    container.settings.raw["search"].pop("default_mode")
    context = container.application.operations.request_context()
    after_sync(
        container.application.retrieval,
        context,
        container.application.ingestion.sync_filesystem(context, "fixture"),
    )

    hits = container.application.retrieval.search(context, SearchRequest(query="승인"))

    assert hits and "vector" in hits[0].metadata["retrieval_channels"]
    assert "rerank_degraded" not in hits[0].metadata
    assert container.application.retrieval.result_warnings(context, hits) == []


def _cross_encoder_container(test_container, tmp_path: Path, monkeypatch, embedding, *, default_mode: str):
    """`[models.reranker] backend = "http"` with the model runtime down."""
    from kip import container as container_module

    monkeypatch.setattr(container_module, "HttpRerankerAdapter", lambda **_kwargs: FailingReranker())
    test_container.settings.raw["search"]["lexical_rerank_enabled"] = True
    test_container.settings.raw.setdefault("models", {})["reranker"] = {
        "enabled": True,
        "backend": "http",
        "model": FailingReranker.model,
        "revision": FailingReranker.revision,
    }
    container = _semantic_container(test_container, tmp_path, embedding)
    container.settings.raw["search"]["default_mode"] = default_mode
    return container


def test_a_model_cross_encoder_leaves_lexical_mode_and_the_fallback_on_bm25(
    test_container, tmp_path: Path, monkeypatch
) -> None:
    from kip.adapters.rerankers import Bm25RerankerAdapter

    degraded = _cross_encoder_container(
        test_container, tmp_path, monkeypatch, ReviewedEmbedding(fail=True), default_mode="reranked"
    )
    assert isinstance(degraded.lexical_reranker, Bm25RerankerAdapter)
    assert degraded.reranker is not None and degraded.reranker.model == FailingReranker.model
    context = degraded.application.operations.request_context()
    degraded.application.ingestion.sync_filesystem(context, "fixture")
    retrieval = degraded.application.retrieval

    lexical = retrieval.search(context, SearchRequest(query="승인"), mode="lexical")
    fallback = retrieval.search(context, SearchRequest(query="승인"))

    for hits in (lexical, fallback):
        assert hits and hits[0].metadata["rerank_rank"] == 1
        assert hits[0].metadata["rerank_model"] == Bm25RerankerAdapter.model
        assert "lexical_rerank_degraded" not in hits[0].metadata
    assert retrieval.result_warnings(context, lexical) == []
    assert retrieval.result_warnings(context, fallback) == ["semantic_degraded"]
    traces = degraded.application.telemetry.list_traces(context.model_copy(update={"roles": ["admin"]}))
    rerankers = [
        [model.model for model in trace.models if model.role == "reranker"]
        for trace in traces
        if trace.route == "search"
    ]
    assert rerankers == [[Bm25RerankerAdapter.model], [Bm25RerankerAdapter.model]]


def test_a_model_cross_encoder_leaves_hybrid_alone_and_fails_explicit_reranked(
    test_container, tmp_path: Path, monkeypatch
) -> None:
    container = _cross_encoder_container(
        test_container, tmp_path, monkeypatch, ReviewedEmbedding(), default_mode="hybrid"
    )
    context = container.application.operations.request_context()
    after_sync(
        container.application.retrieval,
        context,
        container.application.ingestion.sync_filesystem(context, "fixture"),
    )
    retrieval = container.application.retrieval

    hybrid = retrieval.search(context, SearchRequest(query="승인"))

    assert hybrid and "vector" in hybrid[0].metadata["retrieval_channels"]
    assert "rerank_rank" not in hybrid[0].metadata
    assert retrieval.result_warnings(context, hybrid) == []
    with pytest.raises(DependencyUnavailableError, match="reranker down"):
        retrieval.search(context, SearchRequest(query="승인"), mode="reranked")


def test_lexical_reranker_table_defaults_match_the_shipped_bm25_reranker(test_container) -> None:
    import tomllib

    from kip.adapters.repository.memory import MemoryRepository
    from kip.adapters.rerankers import Bm25RerankerAdapter, RapidFuzzRerankerAdapter

    shipped = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "config/kip.example.toml").read_text(encoding="utf-8")
    )["models"]["reranker"]
    assert shipped["backend"] == "bm25"
    settings = test_container.settings
    models = settings.raw.setdefault("models", {})

    models["reranker"] = dict(shipped)
    reference = build_container(settings, repository=MemoryRepository(), load_models=False).reranker
    models["reranker"] = {**shipped, "backend": "http"}
    split = build_container(settings, repository=MemoryRepository(), load_models=False)

    assert split.reranker is None  # the model reranker is not loaded here
    assert isinstance(reference, Bm25RerankerAdapter)
    assert isinstance(split.lexical_reranker, Bm25RerankerAdapter)
    assert (split.lexical_reranker.max_document_chars, split.lexical_reranker.k1, split.lexical_reranker.b) == (
        reference.max_document_chars,
        reference.k1,
        reference.b,
    )

    models["lexical_reranker"] = {"backend": "rapidfuzz"}
    rapidfuzz = build_container(settings, repository=MemoryRepository(), load_models=False).lexical_reranker
    assert isinstance(rapidfuzz, RapidFuzzRerankerAdapter)
    assert (rapidfuzz.max_document_chars, rapidfuzz.baseline_weight) == (8000, 0.15)

    models["lexical_reranker"] = {"backend": "http"}
    with pytest.raises(ConfigurationError, match="bm25 or rapidfuzz"):
        build_container(settings, repository=MemoryRepository(), load_models=False)


def test_capabilities_never_count_vectors_before_the_first_activation(
    test_container, tmp_path: Path, monkeypatch
) -> None:
    from kip.adapters.repository.memory.retrieval import MemoryRetrievalStore

    def counting(*_args, **_kwargs):
        raise AssertionError("capabilities must not count vectors")

    container = _semantic_container(test_container, tmp_path, CustomEmbedding())
    context = container.application.operations.request_context()
    operations = container.application.operations

    monkeypatch.setattr(MemoryRetrievalStore, "semantic_status", counting)
    assert operations.capabilities(context).semantic_projection_status == "missing"
    monkeypatch.undo()
    after_sync(
        container.application.retrieval,
        context,
        container.application.ingestion.sync_filesystem(context, "fixture"),
    )
    monkeypatch.setattr(MemoryRetrievalStore, "semantic_status", counting)

    capabilities = operations.capabilities(context)
    assert (capabilities.semantic_projection_status, capabilities.semantic_search) == ("shadow", False)


def test_runtime_outage_degrades_even_when_the_reranker_is_disabled(test_container, tmp_path: Path) -> None:
    _semantic_container(test_container, tmp_path, ReviewedEmbedding(fail=True))
    raw = test_container.settings.raw
    raw["search"].update({"default_mode": "hybrid", "lexical_rerank_enabled": True})
    raw["models"]["reranker"] = {"enabled": False}
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=ReviewedEmbedding(fail=True),
    )
    context = container.application.operations.request_context()
    after_sync(
        container.application.retrieval,
        context,
        container.application.ingestion.sync_filesystem(context, "fixture"),
    )

    hits = container.application.retrieval.search(context, SearchRequest(query="승인"))

    assert hits and hits[0].metadata["semantic_degraded"] is True
    assert hits[0].metadata["lexical_rerank_degraded"] is True
    with pytest.raises(DependencyUnavailableError):
        container.application.retrieval.search(context, SearchRequest(query="승인"), mode="lexical")


def test_a_runtime_serving_another_model_degrades_default_search_to_lexical(
    test_container, tmp_path: Path
) -> None:
    """Infinity answers `/embeddings` with HTTP 200 for a model it does not
    serve, so an operator evaluating a candidate through KIP_EMBEDDING_MODEL
    would otherwise embed queries in a space the active projection never used.
    """
    import httpx

    from kip.adapters.embeddings.http import HttpEmbeddingAdapter

    def runtime(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": [{"id": "kip-arctic-embed-l-v2-ko"}]})
        raise AssertionError("a runtime serving another model must never be embedded in")

    container = _semantic_container(
        test_container,
        tmp_path,
        HttpEmbeddingAdapter(
            base_url="http://127.0.0.1:7997",
            model=ReviewedEmbedding.model,
            revision=ReviewedEmbedding.revision,
            dimensions=ReviewedEmbedding.dimensions,
            query_instruction=REVIEWED_INSTRUCTION,
            client=httpx.Client(transport=httpx.MockTransport(runtime)),
        ),
    )
    context = container.application.operations.request_context()

    summary = after_sync(
        container.application.retrieval,
        context,
        container.application.ingestion.sync_filesystem(context, "fixture"),
    )

    assert summary.inserted == 2
    update = summary.semantic_projection
    assert update is not None and update.status == "unavailable"
    assert update.reason and "not the configured 'kip-qwen3-embedding-0.6b'" in update.reason
    hits = container.application.retrieval.search(context, SearchRequest(query="승인"))
    assert hits and hits[0].metadata["semantic_degraded"] is True
    assert container.application.retrieval.result_warnings(context, hits) == ["semantic_degraded"]
    # An explicit vector request never silently falls back to another space.
    with pytest.raises(DependencyUnavailableError, match="not the configured"):
        container.application.retrieval.search(context, SearchRequest(query="승인"), mode="vector")
