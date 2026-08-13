from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import anyio
from typer.testing import CliRunner

from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.repository.postgres import PostgresRepository
from kip.cli import app
from kip.container import build_container
from kip.domain.models import RequestContext, SearchRequest
from kip.mcp_server import create_server

SDK_PATH = Path(__file__).resolve().parents[1] / "sdk/python"
sys.path.insert(0, str(SDK_PATH))

from kip_client import KipClient  # noqa: E402


class FixtureEmbedding:
    name = "fixture"
    provider = "fixture"
    model = "fixture-embedding"
    revision = "v1"
    dimensions = 3
    normalized = True

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _text in texts]


def test_search_request_is_the_complete_edge_contract() -> None:
    # Given every supported retrieval control.
    request = SearchRequest(
        query="승인 기준",
        limit=7,
        mode="hybrid",
        source_kinds=["filesystem"],
        document_types=["procedure"],
        project_ids=["project-a"],
        include_candidate_assertions=True,
    )

    # When the request crosses a typed edge.
    payload = request.model_dump(mode="json")

    # Then no edge-specific field is lost.
    assert payload == {
        "query": "승인 기준",
        "limit": 7,
        "mode": "hybrid",
        "source_kinds": ["filesystem"],
        "document_types": ["procedure"],
        "project_ids": ["project-a"],
        "include_candidate_assertions": True,
    }


def test_cli_search_builds_the_complete_search_request(
    test_container,
    monkeypatch,
) -> None:
    # Given a CLI runtime whose retrieval request is observable.
    captured: list[SearchRequest] = []

    def record_search(
        context: RequestContext,
        request: SearchRequest,
        *,
        mode: str | None = None,
    ) -> list[object]:
        captured.append(request)
        return []

    monkeypatch.setattr(
        "kip.cli.build_container",
        lambda settings, load_models=True: test_container,
    )
    monkeypatch.setattr(
        test_container.application.retrieval,
        "search",
        record_search,
    )

    # When every public retrieval option is supplied.
    result = CliRunner().invoke(
        app,
        [
            "search",
            "승인 기준",
            "--limit",
            "7",
            "--mode",
            "hybrid",
            "--source-kind",
            "filesystem",
            "--document-type",
            "procedure",
            "--project-id",
            "project-a",
            "--include-candidate-assertions",
        ],
    )

    # Then the application receives exactly the canonical request.
    assert result.exit_code == 0, result.output
    assert captured == [
        SearchRequest(
            query="승인 기준",
            limit=7,
            mode="hybrid",
            source_kinds=["filesystem"],
            document_types=["procedure"],
            project_ids=["project-a"],
            include_candidate_assertions=True,
        )
    ]


def test_mcp_search_accepts_the_complete_search_request(
    test_container,
    monkeypatch,
) -> None:
    # Given the MCP edge over the same application container.
    captured: list[SearchRequest] = []

    def record_search(
        context: RequestContext,
        request: SearchRequest,
        *,
        mode: str | None = None,
    ) -> list[object]:
        captured.append(request)
        return []

    monkeypatch.setattr("kip.mcp_server.build_container", lambda: test_container)
    monkeypatch.setattr(
        test_container.application.retrieval,
        "search",
        record_search,
    )
    server = create_server()

    async def invoke() -> str:
        result = await server.call_tool(
            "kip_search",
            {
                "query": "승인 기준",
                "limit": 7,
                "mode": "lexical",
                "source_kinds": ["filesystem"],
                "document_types": ["procedure"],
                "project_ids": ["project-a"],
                "include_candidate_assertions": True,
            },
        )
        return result[0][0].text

    # When the complete request is sent through FastMCP.
    payload = json.loads(anyio.run(invoke))

    # Then it is accepted as a normal search result.
    assert isinstance(payload, list)
    assert captured[0].model_dump(mode="json") == {
        "query": "승인 기준",
        "limit": 7,
        "mode": "lexical",
        "source_kinds": ["filesystem"],
        "document_types": ["procedure"],
        "project_ids": ["project-a"],
        "include_candidate_assertions": True,
    }


def test_sdk_search_sends_the_complete_search_request(monkeypatch) -> None:
    # Given a dependency-light SDK client with its HTTP boundary captured.
    captured: dict[str, object] = {}

    def request(
        self: KipClient,
        method: str,
        path: str,
        *,
        admin: bool = False,
        **kwargs: object,
    ) -> list[object]:
        captured.update(method=method, path=path, admin=admin, kwargs=kwargs)
        return []

    monkeypatch.setattr(KipClient, "_request", request)

    # When every canonical control is supplied through the SDK.
    result = KipClient().search(
        "승인 기준",
        limit=7,
        mode="hybrid",
        source_kinds=["filesystem"],
        document_types=["procedure"],
        project_ids=["project-a"],
        include_candidate_assertions=True,
    )

    # Then the REST JSON body is identical to SearchRequest.
    assert result == []
    assert captured["kwargs"] == {
        "json": {
            "query": "승인 기준",
            "limit": 7,
            "mode": "hybrid",
            "source_kinds": ["filesystem"],
            "document_types": ["procedure"],
            "project_ids": ["project-a"],
            "include_candidate_assertions": True,
        }
    }


def test_explicit_cli_scope_replaces_ambient_scope(
    test_container,
    monkeypatch,
) -> None:
    # Given evidence visible only to the ambient workspace scope.
    source = test_container.settings.project_root / "source" / "scope-bound.txt"
    source.write_text("범위교체 전용 증거", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    monkeypatch.setattr(
        "kip.cli.build_container",
        lambda settings, load_models=True: test_container,
    )

    # When an explicit outsider scope is supplied while the environment has access.
    result = CliRunner().invoke(
        app,
        [
            "--acl-scope",
            "project:outside",
            "search",
            "범위교체 전용 증거",
        ],
        env={"KIP_ACL_SCOPES": "workspace:default"},
    )

    # Then the explicit scope replaces, rather than augments, ambient access.
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"] == []


def test_request_context_preserves_an_explicit_empty_scope_set(test_container) -> None:
    # Given an explicit request to evaluate with no ACL scopes.
    # When the application creates the request context.
    context = test_container.application.operations.request_context(acl_scopes=[])

    # Then it does not silently grant the workspace default scope.
    assert context.acl_scopes == []


def test_semantic_capability_requires_an_active_complete_space(test_container) -> None:
    # Given semantic configuration and a working adapter but no active space.
    raw = deepcopy(test_container.settings.raw)
    raw.setdefault("search", {})["semantic_enabled"] = True
    settings = replace(test_container.settings, raw=raw)
    container = build_container(
        settings,
        repository=MemoryRepository(),
        embedding=FixtureEmbedding(),
    )
    context = container.application.operations.request_context()

    # When capability is inspected before and after activating a complete space.
    before = container.application.operations.capabilities(context)
    rebuilt = container.application.retrieval.rebuild_semantic_projection(context)
    container.application.retrieval.activate_semantic_projection(
        context,
        str(rebuilt["space_id"]),
    )
    after = container.application.operations.capabilities(context)

    # Then configuration is visible, but readiness is true only after activation.
    assert before.semantic_search_configured is True
    assert before.semantic_search is False
    assert before.semantic_projection_status == "missing"
    assert after.semantic_search_configured is True
    assert after.semantic_search is True
    assert after.semantic_projection_status == "active"


def test_production_migrations_include_the_1024d_hnsw_index() -> None:
    # Given the automatically applied production migration set.
    migration = Path("migrations/0018_embeddings_1024_hnsw.sql")

    # When its pgvector projection index is inspected.
    sql = migration.read_text(encoding="utf-8")

    # Then approximate search is part of the normal deployment path.
    assert "USING hnsw" in sql
    assert "embedding vector_cosine_ops" in sql
    assert "m = 16" in sql
    assert "ef_construction = 128" in sql
    assert "SET LOCAL statement_timeout = 0" in sql


def test_postgres_repository_retains_bounded_hnsw_scan_configuration() -> None:
    repository = PostgresRepository(
        "postgresql://kip:kip@127.0.0.1:5432/kip",
        hnsw_ef_search=200,
        hnsw_max_scan_tuples=100_000,
    )

    assert repository.database.hnsw_ef_search == 200
    assert repository.database.hnsw_max_scan_tuples == 100_000


def test_vector_query_keeps_the_hnsw_ordering_operator_indexable(monkeypatch) -> None:
    class RecordingCursor:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def execute(self, statement, parameters) -> None:
            self.statements.append(statement)

        def fetchall(self) -> list[object]:
            return []

    class RecordingConnection:
        def __init__(self, cursor: RecordingCursor) -> None:
            self._cursor = cursor

        def cursor(self) -> RecordingCursor:
            return self._cursor

    cursor = RecordingCursor()
    connection = RecordingConnection(cursor)

    @contextmanager
    def recording_connection(context):
        yield connection

    repository = PostgresRepository("postgresql://kip:kip@127.0.0.1:5432/kip")
    monkeypatch.setattr(repository.database, "_connection", recording_connection)

    repository.database.vector_search(
        RequestContext(
            workspace="default",
            principal_id="principal",
            acl_scopes=["workspace:default"],
        ),
        SearchRequest(query="승인 기준", limit=10),
        [0.0] * 1024,
        space_id="space_fixture",
        limit=10,
    )

    search_sql = cursor.statements[-1]
    assert "WITH nearest AS MATERIALIZED" in search_sql
    assert "ORDER BY v.embedding <=> %s::vector\n                LIMIT" in search_sql
    assert "ORDER BY v.embedding <=> %s::vector, l.unit_id" not in search_sql
    assert "OFFSET 0" in search_sql
