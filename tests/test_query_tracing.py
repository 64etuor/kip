from __future__ import annotations

from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError

from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.telemetry.otel import OpenTelemetryQueryTraceExporter
from kip.api import create_app
from kip.container import build_container
from kip.domain.generation import (
    GeneratedClaim,
    GenerationRequest,
    GenerationResult,
    GenerationUsage,
    ModelRevision,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)
from kip.domain.models import AnswerRequest, SearchRequest
from kip.domain.telemetry import QueryTrace
from kip.errors import AuthorizationError
from kip.settings import Settings


class FixtureGenerator:
    name = "fixture"
    provider = "local"
    model = "fixture-model"
    revision = "fixture-revision"

    def generate(self, request: GenerationRequest) -> GenerationResult:
        return GenerationResult(
            claims=(
                GeneratedClaim(
                    text="제출기한은 2026년 8월 15일이다.",
                    evidence_ids=(request.evidence[0].id,),
                    certainty="supported",
                ),
            ),
            model=ModelRevision(
                provider=self.provider,
                model=self.model,
                revision=self.revision,
            ),
            usage=GenerationUsage(input_tokens=20, output_tokens=8, total_tokens=28),
            provider_request_id="provider-secret-request-id",
        )

    def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        raise AssertionError("answer tracing must use grounded generation")


class FailingTraceStore:
    name = "failing"

    def record(self, context, trace) -> None:
        raise RuntimeError("telemetry backend unavailable")

    def list_traces(self, context, *, request_id=None, limit=100):
        raise RuntimeError("telemetry backend unavailable")

    def delete_before(self, context, before):
        raise RuntimeError("telemetry backend unavailable")


class FakeSpan:
    def __init__(self, attributes) -> None:
        self.attributes = dict(attributes)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def set_attribute(self, name, value) -> None:
        self.attributes[name] = value


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []

    def start_as_current_span(self, name, *, attributes):
        span = FakeSpan({"span.name": name, **attributes})
        self.spans.append(span)
        return span


class FakeInstrument:
    def __init__(self) -> None:
        self.values: list[tuple[float, dict]] = []

    def add(self, value, attributes) -> None:
        self.values.append((value, attributes))

    def record(self, value, attributes) -> None:
        self.values.append((value, attributes))


class FakeMeter:
    def __init__(self) -> None:
        self.counter = FakeInstrument()
        self.histogram = FakeInstrument()

    def create_counter(self, name, *, unit, description):
        return self.counter

    def create_histogram(self, name, *, unit, description):
        return self.histogram

def _container(
    tmp_path: Path,
    *,
    generator: FixtureGenerator | None = None,
    repository: MemoryRepository | None = None,
):
    source_root = tmp_path / "source"
    source_root.mkdir()
    generation = {
        "enabled": generator is not None,
        "provider": "local",
        "base_url": "http://127.0.0.1:7998",
        "model": generator.model if generator else "disabled-model",
        "revision": generator.revision if generator else "disabled-revision",
        "allowed_classifications": ["internal"],
    }
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "search": {"semantic_enabled": False},
            "telemetry": {"query_traces_enabled": True},
            "models": {"generation": generation},
            "graph": {"backend": "memory"},
            "sources": {
                "filesystem": [
                    {
                        "name": "fixture",
                        "root": str(source_root),
                        "enabled": True,
                        "read_only": True,
                        "settle_seconds": 0,
                        "include_extensions": [".txt"],
                        "exclude_globs": [],
                        "acl_scope": "workspace:default",
                        "classification": "internal",
                    }
                ]
            },
            "parsers": {"hwp": {"order": ["paired_pdf"]}},
        },
        environment="test",
        workspace="default",
        database_url="memory://",
        cas_path=tmp_path / "cas",
        api_key="test-api-key",
        admin_key="test-admin-key",
    )
    return build_container(
        settings,
        repository=repository or MemoryRepository(),
        generator=generator,
    )


def _ingest(container, body: str) -> None:
    (container.settings.project_root / "source" / "evidence.txt").write_text(
        body,
        encoding="utf-8",
    )
    container.application.ingestion.sync_filesystem(
        container.application.operations.request_context(),
        "fixture",
    )


def test_search_trace_is_structured_and_contains_no_raw_content(tmp_path: Path) -> None:
    secret = "kim@example.test sk-live-do-not-store"
    container = _container(tmp_path)
    _ingest(container, f"승인 문서 본문 {secret}")
    context = container.application.operations.request_context(
        principal_id="person-kim@example.test",
    )

    hits = container.application.retrieval.search(
        context,
        SearchRequest(
            query=f"승인 {secret}",
            limit=5,
            source_kinds=["filesystem"],
        ),
    )
    traces = container.application.telemetry.list_traces(
        context.model_copy(update={"roles": ["admin"]})
    )

    assert hits
    assert len(traces) == 1
    trace = traces[0]
    assert trace.route == "search"
    assert trace.stages == ["acl_prefilter", "lexical"]
    assert trace.filters.source_kind_count == 1
    assert trace.candidates[0].unit_id == hits[0].unit_id
    assert trace.candidates[0].rank == 1
    serialized = trace.model_dump_json()
    assert secret not in serialized
    assert "person-kim@example.test" not in serialized
    assert hits[0].snippet not in serialized


def test_answer_trace_records_selected_evidence_model_usage_and_outcome(
    tmp_path: Path,
) -> None:
    generator = FixtureGenerator()
    container = _container(tmp_path, generator=generator)
    _ingest(container, "정산 증빙 제출기한은 2026년 8월 15일이다.")
    context = container.application.operations.request_context()

    response = container.application.answering.answer(
        context,
        AnswerRequest(query="정산 증빙 제출기한", limit=5),
    )
    traces = container.application.telemetry.list_traces(
        context.model_copy(update={"roles": ["admin"]})
    )
    answer_trace = next(trace for trace in traces if trace.route == "answer")

    assert response.refused is False
    assert answer_trace.outcome == "succeeded"
    assert answer_trace.selected_evidence_ids == [response.citations[0].unit_id]
    assert answer_trace.models[0].model == generator.model
    assert answer_trace.models[0].revision == generator.revision
    assert answer_trace.usage is not None
    assert answer_trace.usage.total_tokens == 28
    serialized = answer_trace.model_dump_json()
    assert response.answer not in serialized
    assert response.query not in serialized
    assert "provider-secret-request-id" not in serialized


def test_trace_schema_rejects_untrusted_payload_fields() -> None:
    with pytest.raises(PydanticValidationError):
        QueryTrace.model_validate(
            {
                "id": "qtrace_0123456789abcdef0123456789abcdef",
                "route": "search",
                "outcome": "succeeded",
                "duration_ms": 1,
                "query": "must not be representable",
            }
        )


def test_query_trace_inspection_requires_admin_role(tmp_path: Path) -> None:
    container = _container(tmp_path)
    context = container.application.operations.request_context()

    with pytest.raises(AuthorizationError, match="admin role"):
        container.application.telemetry.list_traces(context)


def test_otel_export_contains_only_bounded_operational_attributes() -> None:
    tracer = FakeTracer()
    meter = FakeMeter()
    exporter = OpenTelemetryQueryTraceExporter(tracer=tracer, meter=meter)
    trace = QueryTrace(
        route="answer",
        outcome="refused",
        duration_ms=12.5,
        selected_evidence_ids=["unit_secret_identifier"],
        refusal_reason="no_fresh_evidence",
    )

    exporter.export(trace)

    serialized = repr(tracer.spans[0].attributes)
    assert "unit_secret_identifier" not in serialized
    assert tracer.spans[0].attributes["kip.trace.evidence_count"] == 1
    assert meter.counter.values[0][1]["outcome"] == "refused"
    assert meter.histogram.values[0][0] == 12.5


def test_admin_api_exposes_redacted_trace_contract(tmp_path: Path) -> None:
    container = _container(tmp_path)
    _ingest(container, "정산 증빙 제출기한은 2026년 8월 15일이다.")
    client = TestClient(create_app(container))
    query = "정산 증빙 제출기한 private@example.test"

    search_response = client.post(
        "/v1/search",
        headers={"X-KIP-API-Key": "test-api-key"},
        json={"query": query, "limit": 5},
    )
    trace_response = client.get(
        "/v1/admin/query-traces",
        headers={
            "X-KIP-API-Key": "test-api-key",
            "X-KIP-Admin-Key": "test-admin-key",
        },
    )

    assert search_response.status_code == 200
    assert trace_response.status_code == 200
    payload = trace_response.json()["data"]
    assert payload[0]["route"] == "search"
    assert query not in trace_response.text


def test_retention_prune_removes_only_expired_workspace_traces(tmp_path: Path) -> None:
    repository = MemoryRepository()
    container = _container(tmp_path, repository=repository)
    now = datetime(2026, 8, 9, tzinfo=UTC)
    context = container.application.operations.request_context()
    repository.telemetry.record(
        context,
        QueryTrace(
            route="search",
            outcome="succeeded",
            started_at=now - timedelta(days=31),
            duration_ms=1,
        ),
    )
    repository.telemetry.record(
        context,
        QueryTrace(
            route="search",
            outcome="succeeded",
            started_at=now - timedelta(days=29),
            duration_ms=1,
        ),
    )
    admin = context.model_copy(update={"roles": ["admin"]})

    deleted = container.application.telemetry.prune(admin, now=now)

    assert deleted == 1
    assert len(container.application.telemetry.list_traces(admin)) == 1


def test_otlp_http_exporter_sends_trace_and_metric_protobuf() -> None:
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    received: list[tuple[str, str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            received.append(
                (
                    self.path,
                    self.headers.get("Content-Type", ""),
                    self.rfile.read(length),
                )
            )
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    exporter = OpenTelemetryQueryTraceExporter(
        endpoint=f"http://127.0.0.1:{server.server_port}",
    )
    try:
        exporter.export(
            QueryTrace(
                route="search",
                outcome="succeeded",
                duration_ms=2.5,
            )
        )
        assert exporter.force_flush()
        exporter.shutdown()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert {item[0] for item in received} == {"/v1/traces", "/v1/metrics"}
    assert all(item[1] == "application/x-protobuf" for item in received)
    assert all(item[2] for item in received)


def test_trace_backend_failure_never_changes_search_semantics(tmp_path: Path) -> None:
    repository = MemoryRepository()
    repository.telemetry = FailingTraceStore()
    container = _container(tmp_path, repository=repository)
    _ingest(container, "참여율 변경을 승인한다.")
    context = container.application.operations.request_context()

    hits = container.application.retrieval.search(
        context,
        SearchRequest(query="참여율 변경 승인"),
    )

    assert hits
