from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest

from kip.adapters.repository.postgres import PostgresRepository
from kip.container import build_container
from kip.domain.models import ContentUnit, DocumentPacket, ExtractionRun
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
