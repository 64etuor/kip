from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import Mock

import pytest

from kip.application.semantic import SemanticProjectionUseCases
from kip.container import build_container
from kip.domain.embedding import EmbeddingProjectionProgress
from kip.domain.models import EmbeddableUnit
from kip.errors import DependencyUnavailableError


class CountingEmbedding:
    name = "fixture"
    provider = "fixture"
    model = "fixture-embedding"
    revision = "v1"
    dimensions = 3
    normalized = True

    def __init__(self) -> None:
        self.document_calls = 0
        self.document_batches: list[list[str]] = []

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_calls += 1
        self.document_batches.append(list(texts))
        return [[1.0, 0.0, 0.0] for _text in texts]


class FailingV2Embedding(CountingEmbedding):
    revision = "v2"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_calls += 1
        raise DependencyUnavailableError("fixture sidecar unavailable")


def test_semantic_rebuild_skips_current_embeddings(
    test_container,
    tmp_path: Path,
) -> None:
    # Given one current unit that already has an embedding in the configured space.
    source_root = tmp_path / "source"
    (source_root / "근거.txt").write_text("참여율 변경 승인 근거", encoding="utf-8")
    embedding = CountingEmbedding()
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=embedding,
    )
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    container.application.retrieval.rebuild_semantic_projection(context)

    # When the same semantic space is rebuilt again.
    rebuilt = container.application.retrieval.rebuild_semantic_projection(context)

    # Then the existing current vector is reused without another model call.
    assert embedding.document_calls == 1
    assert rebuilt["projection"] == "semantic"
    assert rebuilt["status"] == "shadow"
    assert rebuilt["revision"] == "v1"
    assert rebuilt["dimensions"] == 3
    assert rebuilt["newly_indexed_units"] == 0
    assert rebuilt["indexed_units"] == rebuilt["content_units"] == 1
    assert rebuilt["in_sync"] is True


def test_semantic_verification_uses_current_projection_units(
    test_container,
    tmp_path: Path,
) -> None:
    # Given one current searchable unit in the configured semantic space.
    source_root = tmp_path / "source"
    (source_root / "근거.txt").write_text("참여율 변경 승인 근거", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    embedding = CountingEmbedding()
    semantic = SemanticProjectionUseCases(
        test_container.settings,
        test_container.repository.retrieval,
        embedding,
    )
    semantic.rebuild(context)

    # When projection completeness is verified.
    verification = semantic.verify(context)

    # Then the complete current projection verifies cleanly.
    assert verification["ok"] is True
    assert verification["content_units"] == 1
    assert verification["indexed_units"] == 1


def test_embedding_space_identity_ignores_operational_batch_settings(
    test_container,
) -> None:
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=CountingEmbedding(),
    )
    context = container.application.operations.request_context()
    embedding_config = test_container.settings.raw.setdefault("models", {}).setdefault(
        "embedding", {}
    )
    embedding_config.update({"batch_size": 16, "timeout_seconds": 30})
    original = container.application.retrieval.embedding_space(context)

    embedding_config["batch_size"] = 1
    embedding_config["timeout_seconds"] = 999
    changed = container.application.retrieval.embedding_space(context)

    assert changed.id == original.id
    assert changed.name == original.name


def test_embedding_input_cap_is_bounded_and_versioned(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "긴근거.txt").write_text(
        "HEAD" + "가" * 200 + "TAIL",
        encoding="utf-8",
    )
    embedding_config = test_container.settings.raw.setdefault("models", {}).setdefault(
        "embedding", {}
    )
    embedding_config["max_document_chars"] = 40
    embedding = CountingEmbedding()
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=embedding,
    )
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    bounded_space = container.application.retrieval.embedding_space(context)

    container.application.retrieval.rebuild_semantic_projection(context)

    assert embedding.document_batches
    assert all(len(text) <= 40 for text in embedding.document_batches[0])
    assert embedding.document_batches[0][0].endswith("TAIL")
    assert bounded_space.configuration["document_projection"] == "head_tail_v1"
    embedding_config["max_document_chars"] = 80
    expanded_space = container.application.retrieval.embedding_space(context)
    assert expanded_space.id != bounded_space.id
    assert expanded_space.name != bounded_space.name


def test_embedding_input_cap_defaults_to_widened_bound(
    test_container,
    tmp_path: Path,
) -> None:
    # Given a long document and no explicit max_document_chars override.
    source_root = tmp_path / "source"
    (source_root / "장문.txt").write_text(
        "HEAD" + "가" * 20000 + "TAIL",
        encoding="utf-8",
    )
    embedding = CountingEmbedding()
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=embedding,
    )
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")

    # When the default (unset) embedding space is resolved and rebuilt.
    default_space = container.application.retrieval.embedding_space(context)
    container.application.retrieval.rebuild_semantic_projection(context)

    # Then the widened 2026 default (12000, up from 4000) is used for both
    # the space identity and the actual truncation bound.
    assert default_space.configuration["max_document_chars"] == "12000"
    assert default_space.name.endswith("-c12000-ht1")
    assert embedding.document_batches
    assert all(len(text) <= 12000 for text in embedding.document_batches[0])
    assert embedding.document_batches[0][0].endswith("TAIL")


def test_embedding_space_identity_changes_with_truncation_config(
    test_container,
) -> None:
    # Given a baseline embedding space built from the default cap.
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=CountingEmbedding(),
    )
    context = container.application.operations.request_context()
    baseline = container.application.retrieval.embedding_space(context)

    # When the configured truncation cap changes.
    embedding_config = test_container.settings.raw.setdefault("models", {}).setdefault(
        "embedding", {}
    )
    embedding_config["max_document_chars"] = 20000
    widened = container.application.retrieval.embedding_space(context)

    # Then a brand-new space identity is produced, so a cap change can never
    # silently mix old-truncation and new-truncation vectors in one space;
    # it always requires the existing shadow -> evaluate -> activate flow.
    assert widened.id != baseline.id
    assert widened.name != baseline.name
    assert widened.status == "shadow"


def test_semantic_rebuild_groups_units_by_bounded_input_length(
    test_container,
) -> None:
    # Given pending units in deliberately descending input-length order.
    embedding_config = test_container.settings.raw.setdefault("models", {}).setdefault(
        "embedding", {}
    )
    embedding_config.update({"batch_size": 2, "max_document_chars": 100})
    units = [
        EmbeddableUnit(
            unit_id=unit_id,
            title="",
            body_normalized=body,
            source_hash=unit_id,
        )
        for unit_id, body in (
            ("long", "가" * 90),
            ("short", "가" * 10),
            ("medium", "가" * 50),
        )
    ]
    store = Mock()
    store.save_embedding_space.side_effect = lambda _context, space: space
    store.list_pending_embeddable_units.return_value = units
    store.upsert_embeddings.side_effect = (
        lambda _context, _space_id, records: len(records)
    )
    store.embedding_projection_progress.return_value = EmbeddingProjectionProgress(
        content_units=3,
        indexed_units=3,
    )
    embedding = CountingEmbedding()
    semantic = SemanticProjectionUseCases(
        test_container.settings,
        store,
        embedding,
    )
    context = test_container.application.operations.request_context()

    # When the projection builds model batches.
    semantic.rebuild(context)

    # Then similarly sized inputs are ordered together to avoid padding waste.
    lengths = [
        len(text)
        for batch in embedding.document_batches
        for text in batch
    ]
    assert lengths == sorted(lengths)


def test_failed_rebuild_does_not_replace_active_space(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=CountingEmbedding(),
    )
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    built = container.application.retrieval.rebuild_semantic_projection(context)
    container.repository.retrieval.activate_embedding_space(
        context,
        built["space_id"],
    )
    failing_container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=FailingV2Embedding(),
    )

    with pytest.raises(DependencyUnavailableError):
        failing_container.application.retrieval.rebuild_semantic_projection(context)

    active = test_container.repository.retrieval.active_embedding_space(context)
    assert active is not None
    assert active.id == built["space_id"]
