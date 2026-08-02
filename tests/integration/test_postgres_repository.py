from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from kip.adapters.repository.postgres import PostgresRepository
from kip.container import build_container
from kip.domain.models import (
    AssertionCandidate,
    EmbeddingRecord,
    EmbeddingSpace,
    SearchRequest,
)
from kip.ids import new_id, stable_id
from kip.settings import Settings

URL = os.environ.get("KIP_TEST_POSTGRES_URL") or os.environ.get("KIP_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="PostgreSQL integration URL not configured")


def test_postgres_migrate_ingest_search_and_status(tmp_path: Path):
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
            "database": {"statement_timeout_ms": 15000},
            "search": {"korean_ngram_min": 2, "korean_ngram_max": 4},
            "sources": {
                "filesystem": [
                    {
                        "name": "fixture",
                        "root": str(source_root),
                        "enabled": True,
                        "settle_seconds": 0,
                        "include_extensions": [".txt"],
                        "acl_scope": f"workspace:{workspace}",
                    }
                ]
            },
            "parsers": {"hwp": {"order": ["paired_pdf"]}},
        },
        environment="test",
        workspace=workspace,
        database_url=str(URL),
        cas_path=tmp_path / "cas",
    )
    repository = PostgresRepository(str(URL))
    container = build_container(settings, repository=repository)
    repository.migrate(settings.project_root / "migrations")
    context = container.service.request_context(
        workspace=workspace,
        acl_scopes=[f"workspace:{workspace}"],
    )
    try:
        summary = container.service.sync_filesystem(context, "fixture")
        assert summary.inserted == 1
        hits = container.service.search(context, SearchRequest(query="참여율 변경 승인", limit=10))
        assert hits
        natural_hits = container.service.search(
            context,
            SearchRequest(
                query="A과제에서 참여 비율을 바꾸는 내용은 언제부터 허가된다고 적혀 있는가?",
                limit=10,
            ),
        )
        assert natural_hits
        assert natural_hits[0].document_id == hits[0].document_id
        evidence = container.service.read_unit(context, hits[0].unit_id)
        assert evidence.source_changed_since_index is False

        embeddable = repository.list_embeddable_units(context)
        space = EmbeddingSpace(
            id=stable_id("espace", workspace, "fixture-1024"),
            name="fixture-1024",
            provider="fixture",
            model="fixture",
            revision="fixture-v1",
            dimensions=1024,
            normalized=True,
            status="shadow",
        )
        repository.save_embedding_space(context, space)
        repository.upsert_embeddings(
            context,
            space.id,
            [
                EmbeddingRecord(
                    unit_id=embeddable[0].unit_id,
                    embedding=[1.0] + [0.0] * 1023,
                    source_hash=embeddable[0].source_hash,
                )
            ],
        )
        repository.activate_embedding_space(context, space.id)
        vector_hits = repository.vector_search(
            context,
            SearchRequest(query="표현이 다른 승인 질의", limit=10),
            [1.0] + [0.0] * 1023,
            space_id=space.id,
            limit=10,
        )
        assert vector_hits[0].unit_id == hits[0].unit_id

        candidate = AssertionCandidate(
            id=new_id("cand"),
            subject_id="doc_new",
            predicate="amends",
            object_entity_id="doc_old",
            origin="postgres-integration",
            ontology_version="core/1.0.0",
            evidence=[{"content_unit_id": hits[0].unit_id}],
        )
        container.service.create_candidate(context, candidate)
        assertion = container.service.review_approve(context, candidate.id)
        explanation = container.service.explain_assertion(context, assertion.id)
        assert explanation.assertion.id == assertion.id
        assert explanation.evidence[0].unit.id == hits[0].unit_id

        status = repository.status(context)
        assert status.source_objects == 1
        assert status.content_units == 1
    finally:
        import psycopg

        with (
            psycopg.connect(str(URL), autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("DELETE FROM kip.workspaces WHERE slug=%s", (workspace,))
