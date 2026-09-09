from __future__ import annotations

import copy
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from kip.adapters.repository.postgres import PostgresRepository
from kip.container import build_container
from kip.domain.knowledge import CandidateEvidence
from kip.domain.models import (
    AssertionCandidate,
    ContextRequest,
    EmbeddingRecord,
    EmbeddingSpace,
    GraphNeighborsRequest,
    GraphPathRequest,
    SearchRequest,
)
from kip.errors import NotFoundError
from kip.ids import new_id, stable_id


@pytest.fixture(params=["memory", "postgres", "postgres_nonowner"])
def scoped_container(test_container, request, monkeypatch):
    if request.param == "memory":
        yield test_container
        return
    url = os.environ.get("KIP_TEST_POSTGRES_URL") or os.environ.get("KIP_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration URL not configured")
    import psycopg
    from psycopg import sql

    settings = copy.deepcopy(test_container.settings)
    settings.workspace = "scope_test_" + uuid.uuid4().hex[:12]
    settings.database_url = url
    settings.raw["sources"]["filesystem"][0]["acl_scope"] = f"workspace:{settings.workspace}"
    repository = PostgresRepository(url)
    repository.operations.migrate(Path(__file__).resolve().parents[1] / "migrations")
    role = "scope_reader_" + uuid.uuid4().hex[:12]
    if request.param == "postgres_nonowner":
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role)))
            schemas = [row[0] for row in connection.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'"
            )]
            for schema in schemas:
                connection.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
                connection.execute(sql.SQL("GRANT ALL ON ALL TABLES IN SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
                connection.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
        connect = repository.database._connection

        @contextmanager
        def nonowner(context=None, *, enforce_source_policy=True):
            with connect(context, enforce_source_policy=enforce_source_policy) as connection:
                connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                yield connection

        monkeypatch.setattr(repository.database, "_connection", nonowner)
    try:
        yield build_container(settings, repository=repository)
    finally:
        repository.database.close()
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute("DELETE FROM kip.workspaces WHERE slug=%s", (settings.workspace,))
            if request.param == "postgres_nonowner":
                connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def _index(scoped_container):
    path = scoped_container.settings.project_root / "source" / "allowed.txt"
    path.write_text("uniquerootword 승인 근거", encoding="utf-8")
    context = scoped_container.application.operations.request_context()
    scoped_container.application.ingestion.sync_filesystem(context, "fixture")
    hit = scoped_container.application.retrieval.search(
        context, SearchRequest(query="uniquerootword")
    )[0]
    return path, context, hit


@pytest.mark.parametrize("change", ["remove", "disable", "narrow"])
def test_current_source_configuration_revokes_existing_evidence(scoped_container, change):
    path, context, hit = _index(scoped_container)
    settings = copy.deepcopy(scoped_container.settings)
    sources = settings.raw["sources"]["filesystem"]
    if change == "remove":
        sources.clear()
    elif change == "disable":
        sources[0]["enabled"] = False
    else:
        sources[0]["root"] = str(path.parent / "narrower")
    # Rebuilding the composed app reloads authorization without a sync.
    application = build_container(settings, repository=scoped_container.repository).application
    assert application.retrieval.search(context, SearchRequest(query="uniquerootword")) == []
    assert application.retrieval.vocabulary(context, "uniquerootword") == []
    assert application.retrieval.context_bundle(
        context, ContextRequest(query="uniquerootword")
    ).items == []
    for read in (
        lambda: application.evidence.read_unit(context, hit.unit_id),
        lambda: application.evidence.get_artifact(context, hit.artifact_id),
        lambda: application.evidence.get_document(context, hit.document_id),
    ):
        with pytest.raises(NotFoundError):
            read()


def test_reopen_rejects_source_replaced_by_external_symlink(scoped_container, monkeypatch):
    path, context, hit = _index(scoped_container)
    outside = path.parent.parent / "outside.txt"
    outside.write_text("must never be opened", encoding="utf-8")
    path.unlink()
    path.symlink_to(outside)
    original_open = Path.open

    def guarded_open(self, *args, **kwargs):
        assert self.resolve() != outside.resolve(), "outside source was opened"
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(NotFoundError):
        scoped_container.application.evidence.read_unit(context, hit.unit_id)


def test_root_change_does_not_reauthorize_cached_original_path(scoped_container):
    path, context, hit = _index(scoped_container)
    replacement = path.parent.parent / "new-root"
    replacement.mkdir()
    new_path = replacement / path.name
    new_path.write_bytes(path.read_bytes())
    before = path.stat()
    os.utime(new_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    settings = copy.deepcopy(scoped_container.settings)
    settings.raw["sources"]["filesystem"][0]["root"] = str(replacement)
    application = build_container(settings, repository=scoped_container.repository).application
    summary = application.ingestion.sync_filesystem(context, "fixture")
    assert summary.failed == 0
    new_hit = application.retrieval.search(context, SearchRequest(query="uniquerootword"))[0]
    view = application.evidence.get_artifact(context, new_hit.artifact_id)
    assert view.artifact.source_path == str(new_path.resolve())
    assert new_hit.artifact_id != hit.artifact_id
    assert application.evidence.get_document(context, new_hit.document_id)
    with pytest.raises(NotFoundError):
        application.evidence.get_artifact(context, hit.artifact_id)


def test_cli_workspace_is_applied_before_source_policy_composition(scoped_container, monkeypatch):
    import json

    from typer.testing import CliRunner

    import kip.cli as cli

    settings = copy.deepcopy(scoped_container.settings)
    (settings.project_root / "source" / "cli.txt").write_text("cliworkspaceprobe", encoding="utf-8")
    monkeypatch.setattr(cli.Settings, "load", lambda _config: copy.deepcopy(settings))
    built = []

    def capture(selected, **kwargs):
        container = build_container(selected, repository=scoped_container.repository, **kwargs)
        built.append(container)
        return container

    monkeypatch.setattr(cli, "build_container", capture)
    workspaces = ["cli_scope_" + uuid.uuid4().hex[:12] for _ in range(2)]
    scope = settings.raw["sources"]["filesystem"][0]["acl_scope"]
    snapshot_ids = []
    try:
        for workspace in workspaces:
            result = CliRunner().invoke(cli.app, [
                "--workspace", workspace, "--acl-scope", scope,
                "sync", "run", "--source", "fixture",
            ])
            assert result.exit_code == 0, result.output
            envelope = json.loads(result.stdout)
            assert envelope["data"]["inserted"] == 1, envelope
            assert built[-1].settings.workspace == workspace
            context = built[-1].application.operations.request_context(acl_scopes=[scope])
            hit = built[-1].application.retrieval.search(context, SearchRequest(query="cliworkspaceprobe"))[0]
            view = built[-1].application.evidence.get_artifact(context, hit.artifact_id)
            assert view.source_object.acl_scopes == [scope]
            snapshot_ids.append(view.source_object.acl_snapshot.id)
        assert len(set(snapshot_ids)) == 2
    finally:
        if isinstance(scoped_container.repository, PostgresRepository):
            import psycopg

            with psycopg.connect(settings.database_url, autocommit=True) as connection:
                connection.execute("DELETE FROM kip.workspaces WHERE slug = ANY(%s)", (workspaces,))


def test_scope_filters_before_lexical_vector_vocabulary_and_graph_limits(scoped_container):
    settings = copy.deepcopy(scoped_container.settings)
    outside = settings.project_root / "other-source"
    outside.mkdir()
    (outside / "scopeprefilter.txt").write_text("scopeprefilter scopeforbiddenword", encoding="utf-8")
    (settings.project_root / "source" / "keep.txt").write_text("scopeprefilter allowedword", encoding="utf-8")
    source = copy.deepcopy(settings.raw["sources"]["filesystem"][0])
    source.update(name="other", root=str(outside))
    settings.raw["sources"]["filesystem"].append(source)
    repository = scoped_container.repository
    application = build_container(settings, repository=repository).application
    context = application.operations.request_context(roles=["admin"])
    for source_name in ("fixture", "other"):
        assert application.ingestion.sync_filesystem(context, source_name).inserted == 1
    hits = application.retrieval.search(context, SearchRequest(query="scopeprefilter"))
    by_name = {repository.evidence.get_artifact(context, hit.artifact_id).source_object.system_name: hit for hit in hits}
    assert set(by_name) == {"fixture", "other"}
    space = EmbeddingSpace(
        id=stable_id("espace", context.workspace, "source-scope"), name="source-scope",
        provider="fixture", model="fixture", revision="1", dimensions=1024,
        normalized=True, status="shadow",
    )
    repository.retrieval.save_embedding_space(context, space)
    repository.retrieval.upsert_embeddings(context, space.id, [
        EmbeddingRecord(unit_id=hit.unit_id, source_hash=hit.source_sha256,
                        embedding=([1.0, 0.0] if name == "other" else [0.8, 0.6]) + [0.0] * 1022)
        for name, hit in by_name.items()
    ])
    assertions = {}
    # Synthetic fixture approvals establish evidence-backed graph edges.
    for name in ("fixture", "other", "mixed"):
        evidence_hits = list(by_name.values()) if name == "mixed" else [by_name[name]]
        candidate = AssertionCandidate(
            id=new_id("candidate"), subject_id="scope-center", predicate="mentions",
            object_entity_id="scope-" + name, origin="fixture", ontology_version="core/1.0.0",
            evidence=[CandidateEvidence(content_unit_id=hit.unit_id) for hit in evidence_hits],
        )
        repository.knowledge.save_candidate(context, candidate)
        assertions[name] = repository.knowledge.approve_candidate(context, candidate.id, context.principal_id)
    assert len(repository.knowledge.graph_neighbors(context, GraphNeighborsRequest(node_id="scope-center"))) == 3
    settings.raw["sources"]["filesystem"] = settings.raw["sources"]["filesystem"][:1]
    application = build_container(settings, repository=repository).application
    lexical = application.retrieval.search(context, SearchRequest(query="scopeprefilter", limit=1))
    assert [hit.unit_id for hit in lexical] == [by_name["fixture"].unit_id]
    vector = repository.retrieval.vector_search(
        context, SearchRequest(query="scopeprefilter", limit=1), [1.0] + [0.0] * 1023,
        space_id=space.id, limit=1,
    )
    assert [hit.unit_id for hit in vector] == [by_name["fixture"].unit_id]
    assert repository.retrieval.vocabulary(context, "scopeforbiddenword") == []
    assert repository.retrieval.term_document_frequencies(context, ["scopeforbiddenword"]) == {"scopeforbiddenword": 0}
    neighbors = repository.knowledge.graph_neighbors(context, GraphNeighborsRequest(node_id="scope-center", limit=1))
    assert [edge.assertion_id for edge in neighbors] == [assertions["fixture"].id]
    assert repository.knowledge.graph_path(context, GraphPathRequest(from_node_id="scope-center", to_node_id="scope-other")) == []
    for name in ("other", "mixed"):
        with pytest.raises(NotFoundError):
            repository.knowledge.get_assertion(context, assertions[name].id)
    # A current snapshot is insufficient when the stored artifact path is
    # outside its source root (for example legacy root-move cache corruption).
    allowed_id = by_name["fixture"].artifact_id
    if isinstance(repository, PostgresRepository):
        with repository.database._connection(context) as connection:
            connection.execute("UPDATE content.artifacts SET source_path=%s WHERE id=%s", (str(outside / "keep.txt"), allowed_id))
    else:
        repository.state.artifacts[allowed_id].artifact.source_path = str(outside / "keep.txt")
    assert application.retrieval.search(context, SearchRequest(query="scopeprefilter")) == []
    assert repository.retrieval.vector_search(
        context, SearchRequest(query="scopeprefilter"), [1.0] + [0.0] * 1023,
        space_id=space.id, limit=1,
    ) == []
    assert repository.knowledge.graph_neighbors(context, GraphNeighborsRequest(node_id="scope-center")) == []
    with pytest.raises(NotFoundError):
        application.evidence.get_artifact(context, allowed_id)


@pytest.mark.parametrize("change", ["remove", "external_symlink"])
def test_xlsx_reopen_applies_current_source_scope_before_open(scoped_container, monkeypatch, change):
    from openpyxl import Workbook

    settings = copy.deepcopy(scoped_container.settings)
    path = settings.project_root / "source" / "spreadsheetboundary.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "spreadsheetboundary"
    workbook.active["B1"] = 42
    workbook.save(path)
    workbook.close()
    application = scoped_container.application
    context = application.operations.request_context()
    assert application.ingestion.sync_filesystem(context, "fixture").inserted == 1
    hit = application.retrieval.search(context, SearchRequest(query="spreadsheetboundary"))[0]
    assert application.evidence.read_xlsx(context, hit.artifact_id, sheet="Sheet", cell_range="A1:B1").cells
    if change == "remove":
        settings.raw["sources"]["filesystem"] = []
        application = build_container(settings, repository=scoped_container.repository).application
    else:
        outside = settings.project_root / "outside.xlsx"
        path.rename(outside)
        path.symlink_to(outside)

    def forbidden_read(*args, **kwargs):
        pytest.fail("spreadsheet parser was invoked outside the allowed root")

    monkeypatch.setattr("kip.adapters.storage.local.read_xlsx_range", forbidden_read)
    with pytest.raises(NotFoundError):
        application.evidence.read_xlsx(context, hit.artifact_id, sheet="Sheet", cell_range="A1:B1")


def test_placeholder_reopen_does_not_hash_or_claim_matching_stat(scoped_container, monkeypatch):
    _path, context, hit = _index(scoped_container)
    monkeypatch.setattr("kip.adapters.storage.local.is_cloud_placeholder", lambda _stat: True)

    def forbidden_hash(_path):
        pytest.fail("cloud-only source bytes were opened")

    monkeypatch.setattr("kip.adapters.storage.local._sha256_file", forbidden_hash)
    read = scoped_container.application.evidence.read_unit(context, hit.unit_id, verify_hash=False)
    assert read.current_source_sha256 is None
    assert read.source_changed_since_index is True


def test_cli_rest_mcp_share_source_removal_boundary(scoped_container, monkeypatch):
    import json

    import anyio
    from fastapi.testclient import TestClient
    from mcp.types import TextContent
    from typer.testing import CliRunner

    from kip.api import create_app
    from kip.cli import app
    from kip.mcp_server import create_server

    _path, context, hit = _index(scoped_container)
    settings = copy.deepcopy(scoped_container.settings)
    settings.raw["sources"]["filesystem"] = []
    container = build_container(settings, repository=scoped_container.repository)
    monkeypatch.setattr("kip.cli.Settings.load", lambda _config: settings)
    monkeypatch.setattr("kip.cli.build_container", lambda *_args, **_kwargs: container)
    monkeypatch.setattr("kip.mcp_server.build_container", lambda: container)
    # MCP's context comes from its process environment, never tool arguments.
    monkeypatch.setenv("KIP_WORKSPACE", context.workspace)
    monkeypatch.setenv("KIP_ACL_SCOPES", ",".join(context.acl_scopes))
    runner = CliRunner()
    cli_search = runner.invoke(app, ["search", "uniquerootword"])
    assert json.loads(cli_search.stdout)["data"] == []
    cli_read = runner.invoke(app, ["read", hit.unit_id])
    assert json.loads(cli_read.stderr)["error"]["code"] == "not_found"
    client = TestClient(create_app(container))
    headers = {"X-KIP-API-Key": "test-key"}
    rest_search = client.post("/v1/search", headers=headers, json={"query": "uniquerootword"})
    assert rest_search.json()["data"] == []
    rest_read = client.get(f"/v1/units/{hit.unit_id}", headers=headers)
    assert rest_read.json()["error"]["code"] == "not_found"
    server = create_server()

    async def invoke():
        await server.call_tool("kip_capabilities", {})
        search = await server.call_tool("kip_search", {"query": "uniquerootword"})
        read = await server.call_tool("kip_read", {"unit_id": hit.unit_id})
        assert isinstance(search.content[0], TextContent)
        assert isinstance(read.content[0], TextContent)
        return json.loads(search.content[0].text), json.loads(read.content[0].text)

    mcp_search, mcp_read = anyio.run(invoke)
    assert mcp_search["data"] == []
    assert mcp_read["error"]["code"] == "not_found"
    assert mcp_read["schema_version"] == rest_read.json()["schema_version"] == "kip.envelope.v1"


@pytest.mark.parametrize("complete_operator", [True, False])
def test_acl_change_revokes_old_grants_on_unchanged_files_and_mixed_graph(scoped_container, complete_operator):
    settings = copy.deepcopy(scoped_container.settings)
    old_scope = settings.raw["sources"]["filesystem"][0]["acl_scope"]
    new_scope = old_scope + ":new"
    second_scope = old_scope + ":second"
    other_root = settings.project_root / "second-root"
    other_root.mkdir()
    (other_root / "second.txt").write_text("aclsecondword", encoding="utf-8")
    (settings.project_root / "source" / "scope.txt").write_text("aclchangedword", encoding="utf-8")
    second = copy.deepcopy(settings.raw["sources"]["filesystem"][0])
    second.update(name="second", root=str(other_root), acl_scope=second_scope)
    settings.raw["sources"]["filesystem"].append(second)
    repository = scoped_container.repository
    application = build_container(settings, repository=repository).application
    operator = application.operations.request_context(acl_scopes=[old_scope, new_scope, second_scope], roles=["admin"])
    for source in ("fixture", "second"):
        assert application.ingestion.sync_filesystem(operator, source).inserted == 1
    changed = application.retrieval.search(operator, SearchRequest(query="aclchangedword"))[0]
    other = application.retrieval.search(operator, SearchRequest(query="aclsecondword"))[0]
    assertions = {}
    candidates = {}
    for name, hits in (("single", [changed]), ("mixed", [changed, other])):
        candidate = AssertionCandidate(
            id=new_id("candidate"), subject_id="acl-change-center", predicate="mentions",
            object_entity_id="acl-" + name, origin="fixture", ontology_version="core/1.0.0",
            evidence=[CandidateEvidence(content_unit_id=hit.unit_id) for hit in hits],
        )
        repository.knowledge.save_candidate(operator, candidate)
        assertions[name] = repository.knowledge.approve_candidate(operator, candidate.id, operator.principal_id)
        pending = candidate.model_copy(update={"id": new_id("candidate"), "object_entity_id": "pending-" + name})
        repository.knowledge.save_candidate(operator, pending)
        candidates[name] = pending
    settings.raw["sources"]["filesystem"][0]["acl_scope"] = new_scope
    settings.raw["sources"]["filesystem"][0]["classification"] = "personal"
    application = build_container(settings, repository=repository).application
    sync_context = operator if complete_operator else operator.model_copy(update={"acl_scopes": [old_scope, new_scope]})
    assert application.ingestion.sync_filesystem(sync_context, "fixture").unchanged == 1
    revoked = operator.model_copy(update={"acl_scopes": [old_scope, second_scope]})
    admitted = operator.model_copy(update={"acl_scopes": [new_scope, second_scope]})
    new_only = operator.model_copy(update={"acl_scopes": [new_scope]})
    assert application.retrieval.search(revoked, SearchRequest(query="aclchangedword")) == []
    assert application.retrieval.search(admitted, SearchRequest(query="aclchangedword"))
    for read in (
        lambda: application.evidence.read_unit(revoked, changed.unit_id),
        lambda: application.evidence.get_artifact(revoked, changed.artifact_id),
        lambda: application.evidence.get_document(revoked, changed.document_id),
    ):
        with pytest.raises(NotFoundError):
            read()
    assert application.evidence.read_unit(admitted, changed.unit_id).unit.acl_scopes == [new_scope]
    assert application.evidence.read_unit(admitted, changed.unit_id).unit.classification.value == "personal"
    assert application.evidence.get_artifact(admitted, changed.artifact_id).source_object.acl_scopes == [new_scope]
    assert repository.knowledge.graph_neighbors(revoked, GraphNeighborsRequest(node_id="acl-change-center")) == []
    assert repository.knowledge.graph_path(revoked, GraphPathRequest(from_node_id="acl-change-center", to_node_id="acl-single")) == []
    for name in ("single", "mixed"):
        with pytest.raises(NotFoundError):
            repository.knowledge.get_assertion(revoked, assertions[name].id)
        with pytest.raises(NotFoundError):
            repository.knowledge.get_candidate(revoked, candidates[name].id)
        if complete_operator or name == "single":
            assert repository.knowledge.get_assertion(admitted, assertions[name].id)
        assert repository.knowledge.get_candidate(admitted, candidates[name].id)
    with pytest.raises(NotFoundError):
        repository.knowledge.get_assertion(new_only, assertions["mixed"].id)
    # A stale assertion cache cannot authorize evidence revoked underneath it.
    # Keep current snapshot IDs while simulating old cached assertion grants.
    if isinstance(repository, PostgresRepository):
        with repository.database._connection(operator) as connection:
            connection.execute("UPDATE knowledge.assertions SET acl_scopes=%s WHERE id=%s", ([old_scope], assertions["single"].id))
    else:
        repository.state.assertions[assertions["single"].id].acl_scopes = [old_scope]
    assert repository.knowledge.graph_neighbors(revoked, GraphNeighborsRequest(node_id="acl-change-center")) == []
    with pytest.raises(NotFoundError):
        repository.knowledge.get_assertion(revoked, assertions["single"].id)


def test_root_move_then_content_reversion_keeps_revision_in_current_root(scoped_container):
    path, context, old_hit = _index(scoped_container)
    original = path.read_bytes()
    settings = copy.deepcopy(scoped_container.settings)
    replacement = settings.project_root / "moved-root"
    replacement.mkdir()
    moved = replacement / path.name
    moved.write_text("different revision contents", encoding="utf-8")
    settings.raw["sources"]["filesystem"][0]["root"] = str(replacement)
    application = build_container(settings, repository=scoped_container.repository).application
    assert application.ingestion.sync_filesystem(context, "fixture").replaced == 1
    moved.write_bytes(original)
    assert application.ingestion.sync_filesystem(context, "fixture").replaced == 1
    hit = application.retrieval.search(context, SearchRequest(query="uniquerootword"))[0]
    assert hit.artifact_id != old_hit.artifact_id
    view = application.evidence.get_artifact(context, hit.artifact_id)
    assert view.artifact.source_path == str(moved.resolve())
    assert view.revision.raw_object_uri == moved.resolve().as_uri()
