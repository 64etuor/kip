from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest

from kip.adapters.repository.postgres import PostgresRepository
from kip.container import build_container
from kip.domain.models import (
    ContentUnit,
    DocumentPacket,
    EmbeddingRecord,
    EmbeddingSpace,
    ExtractionRun,
    RequestContext,
    SearchRequest,
)
from kip.errors import ValidationError
from kip.ids import new_id, stable_id
from kip.settings import Settings

URL = os.environ.get("KIP_TEST_POSTGRES_URL") or os.environ.get("KIP_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="PostgreSQL integration URL not configured")


class FixtureEmbedding:
    name = "fixture"
    provider = "fixture"
    model = "fixture-embedding"
    revision = "v1"
    dimensions = 1024
    normalized = True

    def embed_query(self, text: str) -> list[float]:
        return [1.0] + [0.0] * 1023

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 1023 for _text in texts]


def test_postgres_semantic_verification_excludes_inactive_extraction_units(
    tmp_path: Path,
) -> None:
    # Given a current source revision plus an older inactive extraction that was
    # never embedded.
    pytest.importorskip("psycopg")
    workspace = "test_" + uuid.uuid4().hex[:12]
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "근거.txt").write_text(
        "첫 번째 참여율 변경 승인 근거",
        encoding="utf-8",
    )
    settings = Settings(
        project_root=Path(__file__).resolve().parents[2],
        config_path=tmp_path / "kip.toml",
        raw={
            "search": {"semantic_enabled": False},
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
                        "acl_scope": f"workspace:{workspace}",
                    }
                ]
            },
        },
        environment="test",
        workspace=workspace,
        database_url=str(URL),
        cas_path=tmp_path / "cas",
    )
    repository = PostgresRepository(str(URL))
    container = build_container(
        settings,
        repository=repository,
        embedding=FixtureEmbedding(),
    )
    repository.operations.migrate(settings.project_root / "migrations")
    context = container.application.operations.request_context(
        workspace=workspace,
        principal_id="principal_owner",
        acl_scopes=[f"workspace:{workspace}"],
    )

    try:
        first_sync = container.application.ingestion.sync_filesystem(context, "fixture")
        assert first_sync.inserted == 1, first_sync
        original_unit = repository.retrieval.list_embeddable_units(context)[0]
        content_unit = repository.evidence.get_content_unit(
            context,
            original_unit.unit_id,
        )
        view = repository.evidence.get_artifact(context, content_unit.artifact_id)
        assert view.source_object is not None
        assert view.revision is not None
        assert view.document is not None
        extraction_id = new_id("ext")
        replacement = ContentUnit(
            id=stable_id("unit", extraction_id, "0"),
            extraction_id=extraction_id,
            document_id=content_unit.document_id,
            artifact_id=content_unit.artifact_id,
            ordinal=0,
            unit_type="replacement",
            title=content_unit.title,
            body="두 번째 최신 참여율 변경 승인 근거와 추가 문장",
            body_normalized="두 번째 최신 참여율 변경 승인 근거와 추가 문장",
            lexical_text="두 번째 최신 참여율 변경 승인 근거 추가 문장",
            locator=content_unit.locator,
            classification=content_unit.classification,
            acl_scopes=content_unit.acl_scopes,
            acl_snapshot_id=content_unit.acl_snapshot_id,
        )
        repository.ingestion.replace_extraction(
            context,
            DocumentPacket(
                workspace_id=workspace,
                source_object=view.source_object,
                revision=view.revision,
                logical_document=view.document,
                artifact=view.artifact,
                extraction=ExtractionRun(
                    id=extraction_id,
                    artifact_id=view.artifact.id,
                    parser_name="replacement-parser",
                    parser_version="2.0",
                    status="succeeded",
                    quality_score=0.95,
                    output_hash="d" * 64,
                ),
                units=[replacement],
            ),
        )

        # When only the current active extraction is embedded and verified.
        container.application.retrieval.rebuild_semantic_projection(context)
        verification = container.application.retrieval.verify_semantic_projection(
            context,
        )

        # Then the historical unit is excluded from the completeness denominator.
        assert repository.operations.status(context).content_units == 2
        assert verification["ok"] is True
        assert verification["content_units"] == 1
        assert verification["indexed_units"] == 1
    finally:
        import psycopg

        with (
            psycopg.connect(str(URL), autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("DELETE FROM kip.workspaces WHERE slug=%s", (workspace,))


def test_postgres_migrate_installs_the_production_hnsw_index() -> None:
    # Given a PostgreSQL production-profile repository.
    psycopg = pytest.importorskip("psycopg")
    repository = PostgresRepository(str(URL))

    # When the normal migration set is applied.
    repository.operations.migrate(Path(__file__).resolve().parents[2] / "migrations")

    # Then the 1024-dimensional cosine HNSW index is installed automatically.
    with psycopg.connect(str(URL)) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = 'search'
              AND indexname = 'embeddings_1024_hnsw_cosine_idx'
            """
        )
        row = cursor.fetchone()
    assert row is not None
    assert "USING hnsw" in row[0]


def test_postgres_migrate_installs_the_1536_hnsw_index() -> None:
    # Given a PostgreSQL production-profile repository.
    psycopg = pytest.importorskip("psycopg")
    repository = PostgresRepository(str(URL))

    # When the normal migration set is applied.
    repository.operations.migrate(Path(__file__).resolve().parents[2] / "migrations")

    # Then the 1536-dimensional cosine HNSW index is provisioned automatically
    # too, so a second embedding model dimensionality is available without
    # any dynamic DDL.
    with psycopg.connect(str(URL)) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = 'search'
              AND indexname = 'embeddings_1536_hnsw_cosine_idx'
            """
        )
        row = cursor.fetchone()
    assert row is not None
    assert "USING hnsw" in row[0]


def test_postgres_vector_search_round_trips_a_1536_dimensional_space(
    tmp_path: Path,
) -> None:
    # Given content ingested into a workspace with no active semantic
    # projection yet.
    pytest.importorskip("psycopg")
    workspace = "test_" + uuid.uuid4().hex[:12]
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "공문.txt").write_text(
        "A과제 참여율 변경은 2026년 7월 1일부터 승인한다.",
        encoding="utf-8",
    )
    settings = Settings(
        project_root=Path(__file__).resolve().parents[2],
        config_path=tmp_path / "kip.toml",
        raw={
            "search": {"semantic_enabled": False},
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
                        "acl_scope": f"workspace:{workspace}",
                    }
                ]
            },
        },
        environment="test",
        workspace=workspace,
        database_url=str(URL),
        cas_path=tmp_path / "cas",
    )
    repository = PostgresRepository(str(URL))
    container = build_container(settings, repository=repository)
    repository.operations.migrate(settings.project_root / "migrations")
    context = container.application.operations.request_context(
        workspace=workspace,
        principal_id="principal_owner",
        acl_scopes=[f"workspace:{workspace}"],
    )

    try:
        summary = container.application.ingestion.sync_filesystem(context, "fixture")
        assert summary.inserted == 1
        embeddable = repository.retrieval.list_embeddable_units(context)
        assert embeddable

        # When a 1536-dimensional embedding space is created directly
        # against the postgres store (a dimensionality the default 1024
        # settings profile never provisions) and an embedding of that width
        # is upserted and searched.
        space = EmbeddingSpace(
            id=stable_id("espace", workspace, "fixture-1536"),
            name="fixture-1536",
            provider="fixture",
            model="fixture-1536",
            revision="fixture-v1",
            dimensions=1536,
            normalized=True,
            status="shadow",
        )
        assert repository.retrieval.embedding_space_exists(context, space.id) is False
        repository.retrieval.save_embedding_space(context, space)
        # The single-row lookup `capabilities` uses instead of counting vectors.
        assert repository.retrieval.embedding_space_exists(context, space.id) is True
        assert repository.retrieval.embedding_space_exists(context, "espace_missing") is False
        indexed = repository.retrieval.upsert_embeddings(
            context,
            space.id,
            [
                EmbeddingRecord(
                    unit_id=embeddable[0].unit_id,
                    embedding=[1.0] + [0.0] * 1535,
                    source_hash=embeddable[0].source_hash,
                )
            ],
        )
        assert indexed == 1
        vector_hits = repository.retrieval.vector_search(
            context,
            SearchRequest(query="참여율 변경 승인", limit=10),
            [1.0] + [0.0] * 1535,
            space_id=space.id,
            limit=10,
        )

        # Then the round trip through search.embeddings_1536 succeeds and
        # semantic_status counts the vector under the 1536-d space, proving
        # the UNION-ALL status query sees every provisioned table.
        assert vector_hits
        assert vector_hits[0].unit_id == embeddable[0].unit_id
        status = repository.retrieval.semantic_status(context)
        space_vectors = status["space_vectors"]
        assert isinstance(space_vectors, dict)
        assert space_vectors[space.id] == 1
    finally:
        import psycopg

        with (
            psycopg.connect(str(URL), autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("DELETE FROM kip.workspaces WHERE slug=%s", (workspace,))


def test_postgres_save_embedding_space_rejects_an_unprovisioned_dimension() -> None:
    # Given a repository with the production migration set applied.
    pytest.importorskip("psycopg")
    repository = PostgresRepository(str(URL))
    repository.operations.migrate(Path(__file__).resolve().parents[2] / "migrations")
    workspace = "test_" + uuid.uuid4().hex[:12]
    context = RequestContext(
        workspace=workspace,
        principal_id="principal_owner",
        acl_scopes=[f"workspace:{workspace}"],
    )
    space = EmbeddingSpace(
        id=stable_id("espace", workspace, "fixture-768"),
        name="fixture-768",
        provider="fixture",
        model="fixture-768",
        revision="fixture-v1",
        dimensions=768,
        normalized=True,
        status="shadow",
    )

    # When saving an embedding space with a dimensionality PostgreSQL has no
    # provisioned table for.
    # Then it is rejected with a message listing every provisioned
    # dimension, before any row is written (no workspace needs to exist).
    with pytest.raises(ValidationError) as excinfo:
        repository.retrieval.save_embedding_space(context, space)
    assert "768" in str(excinfo.value)
    assert "1024" in str(excinfo.value)
    assert "1536" in str(excinfo.value)


def test_postgres_projection_maintenance_covers_every_scope_and_pages(tmp_path: Path) -> None:
    # Given two sources with different ACL scopes and a caller who sees only one.
    pytest.importorskip("psycopg")
    workspace = "test_" + uuid.uuid4().hex[:12]
    roots = {name: tmp_path / name for name in ("open", "finance")}
    for name, root in roots.items():
        root.mkdir()
        for index in range(3):
            (root / f"{name}-{index}.txt").write_text(f"{name} 문서 {index} 예산 승인 근거", encoding="utf-8")
    settings = Settings(
        project_root=Path(__file__).resolve().parents[2],
        config_path=tmp_path / "kip.toml",
        raw={
            "search": {"semantic_enabled": True, "semantic_auto_activate": True},
            "models": {"embedding": {"page_size": 2}},
            "graph": {"backend": "memory"},
            "sources": {
                "filesystem": [
                    {
                        "name": name,
                        "root": str(root),
                        "enabled": True,
                        "read_only": True,
                        "settle_seconds": 0,
                        "include_extensions": [".txt"],
                        "exclude_globs": [],
                        "acl_scope": f"workspace:{workspace}" if name == "open" else "team:finance",
                    }
                    for name, root in roots.items()
                ]
            },
        },
        environment="test",
        workspace=workspace,
        database_url=str(URL),
        cas_path=tmp_path / "cas",
    )
    repository = PostgresRepository(str(URL))
    container = build_container(settings, repository=repository, embedding=FixtureEmbedding())
    repository.operations.migrate(settings.project_root / "migrations")
    caller = container.application.operations.request_context(
        workspace=workspace, principal_id="principal_open", acl_scopes=[f"workspace:{workspace}"]
    )
    try:
        for name in roots:
            container.application.ingestion.sync_filesystem(caller, name)

        # When maintenance runs from the ordinary caller context.
        update = container.application.retrieval.maintain_semantic_projection(caller)

        # Then units of every scope are embedded, in pages, and counted.
        assert update.status == "updated"
        assert (update.indexed_units, update.content_units, update.newly_indexed_units) == (6, 6, 6)
        assert update.active is False and update.reason and "not reviewed" in update.reason
        system = container.application.retrieval._semantic.projection_context(caller)
        assert "team:finance" in system.acl_scopes
        space_id = container.application.retrieval.embedding_space(caller).id
        assert repository.retrieval.list_pending_embeddable_units(system, space_id) == []

        # And the caller-scoped view and the existence probe still honour ACLs.
        assert repository.retrieval.embedding_projection_progress(caller, space_id).content_units == 3
        assert repository.retrieval.any_term_visible(caller, ["open"]) is True
        assert repository.retrieval.any_term_visible(caller, ["finance"]) is False
        assert repository.retrieval.any_term_visible(system, ["finance"]) is True

        # And pending pages are ordered, bounded and resumable by unit id.
        other_space = EmbeddingSpace(
            id=f"espace_{uuid.uuid4().hex[:12]}",
            name="paging-probe",
            provider="fixture",
            model="fixture-embedding",
            revision="v2",
            dimensions=1024,
            normalized=True,
            status="shadow",
        )
        repository.retrieval.save_embedding_space(system, other_space)
        first = repository.retrieval.list_pending_embeddable_units(system, other_space.id, limit=4)
        rest = repository.retrieval.list_pending_embeddable_units(
            system, other_space.id, after_unit_id=first[-1].unit_id, limit=4
        )
        ids = [unit.unit_id for unit in [*first, *rest]]
        assert len(first) == 4 and len(rest) == 2 and ids == sorted(ids) and len(set(ids)) == 6
    finally:
        import psycopg

        with psycopg.connect(str(URL), autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM kip.workspaces WHERE slug=%s", (workspace,))
