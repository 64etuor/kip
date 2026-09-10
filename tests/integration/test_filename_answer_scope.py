from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from kip.adapters.repository.postgres import PostgresRepository
from kip.container import build_container
from kip.domain.models import AnswerRequest
from kip.settings import Settings

URL = os.environ.get("KIP_TEST_POSTGRES_URL") or os.environ.get("KIP_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="PostgreSQL integration URL not configured")


def test_filename_ambiguity_uses_allowed_documents_before_limit(tmp_path: Path):
    psycopg = pytest.importorskip("psycopg")
    workspace = "filename_" + uuid.uuid4().hex[:12]
    root = Path(__file__).resolve().parents[2]
    repository = PostgresRepository(str(URL))
    roots = []
    for name in ("public", "restricted"):
        source = tmp_path / name
        source.mkdir()
        (source / "사업 승인 의결문.txt").write_text(f"{name} 검토 초안이다.")
        roots.append({
            "name": name, "root": str(source), "enabled": True, "read_only": True,
            "settle_seconds": 0, "include_extensions": [".txt"], "acl_scope": name,
        })
    settings = Settings(
        project_root=root, config_path=tmp_path / "kip.toml", environment="test",
        workspace=workspace, database_url=str(URL), cas_path=tmp_path / "cas",
        raw={"search": {"semantic_enabled": False}, "sources": {"filesystem": roots}},
    )
    try:
        repository.operations.migrate(root / "migrations")
        container = build_container(settings, repository=repository)
        owner = container.application.operations.request_context(acl_scopes=["public", "restricted"])
        for name in ("public", "restricted"):
            assert container.application.ingestion.sync_filesystem(owner, name).inserted == 1
        request = AnswerRequest(query="사업 승인 의결문.txt", limit=1)
        ambiguous = container.application.answering.answer(owner, request)
        assert ambiguous.refusal_reason == "clarification_required"

        reader = owner.model_copy(update={"acl_scopes": ["public"]})
        allowed = container.application.answering.answer(reader, request)
        assert not allowed.refused
        assert allowed.answer == "public 검토 초안이다."
        assert "restricted" not in allowed.model_dump_json()
        assert container.application.answering.answer(
            owner.model_copy(update={"acl_scopes": []}), request,
        ).refused

        # Reloading a removed root must also remove it from ambiguity checks.
        settings.raw["sources"]["filesystem"] = roots[:1]
        reloaded = build_container(settings, repository=PostgresRepository(str(URL)))
        narrowed = reloaded.application.answering.answer(owner, request)
        assert not narrowed.refused
        assert narrowed.answer == "public 검토 초안이다."
    finally:
        with psycopg.connect(str(URL), autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM kip.workspaces WHERE slug=%s", (workspace,))
