from __future__ import annotations

from pathlib import Path

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.application.retrieval import reciprocal_rank_fusion
from kip.container import build_container
from kip.domain.models import EvidenceLocator, SearchHit, SearchRequest
from kip.errors import DependencyUnavailableError
from kip.ports.reranker import RerankScore


class FixtureEmbedding:
    name = "fixture"
    provider = "fixture"
    model = "fixture-embedding"
    revision = "v1"
    dimensions = 3
    normalized = True

    def __init__(self) -> None:
        self.query_calls = 0
        self.document_calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return [1.0, 0.0, 0.0]

    def embed_documents(self, texts) -> list[list[float]]:
        self.document_calls += 1
        return [
            [1.0, 0.0, 0.0] if "허가" in text else [0.0, 1.0, 0.0]
            for text in texts
        ]


class FailingEmbedding(FixtureEmbedding):
    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        raise DependencyUnavailableError("fixture sidecar unavailable")

    def embed_documents(self, texts) -> list[list[float]]:
        self.document_calls += 1
        raise DependencyUnavailableError("fixture sidecar unavailable")


class FixtureReranker:
    name = "fixture"
    provider = "fixture"
    model = "fixture-reranker"
    revision = "v1"

    def __init__(self) -> None:
        self.calls = 0
        self.document_counts: list[int] = []

    def rerank(self, query: str, documents) -> list[RerankScore]:
        self.calls += 1
        self.document_counts.append(len(documents))
        scores = [
            RerankScore(index=index, score=10.0 if "허가" in document else 0.0)
            for index, document in enumerate(documents)
        ]
        return sorted(scores, key=lambda item: (-item.score, item.index))


class CountingMemoryRepository(MemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self.single_lookup_count = 0
        self.bulk_lookup_count = 0

    def get_content_unit(self, context, unit_id):
        self.single_lookup_count += 1
        return super().get_content_unit(context, unit_id)

    def get_content_units(self, context, unit_ids):
        self.bulk_lookup_count += 1
        return super().get_content_units(context, unit_ids)


def _hit(unit_id: str, score: float) -> SearchHit:
    return SearchHit(
        unit_id=unit_id,
        document_id=f"doc_{unit_id}",
        artifact_id=f"art_{unit_id}",
        source_kind="filesystem",
        title=unit_id,
        snippet=unit_id,
        score=score,
        locator=EvidenceLocator(type="text_span", data={}),
        source_uri=f"file:///public/{unit_id}.txt",
        source_sha256="a" * 64,
    )


def test_rrf_uses_rank_and_deduplicates_units() -> None:
    lexical = [_hit("a", 100.0), _hit("b", 90.0)]
    vector = [_hit("b", 0.99), _hit("c", 0.98)]

    fused = reciprocal_rank_fusion(lexical, vector, limit=3, rank_constant=60)

    assert [hit.unit_id for hit in fused] == ["b", "a", "c"]
    assert fused[0].metadata["retrieval_channels"] == ["lexical", "vector"]
    assert fused[0].metadata["lexical_rank"] == 2
    assert fused[0].metadata["vector_rank"] == 1
    assert fused[0].score < 1.0


def test_default_lexical_mode_makes_no_model_call(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    context = test_container.service.request_context()
    test_container.service.sync_filesystem(context, "fixture")
    embedding = FixtureEmbedding()
    test_container.service.embedding = embedding

    hits = test_container.service.search(context, SearchRequest(query="승인"))

    assert hits
    assert embedding.query_calls == 0


def test_embedding_space_identity_ignores_operational_batch_settings(
    test_container,
) -> None:
    context = test_container.service.request_context()
    test_container.service.embedding = FixtureEmbedding()
    embedding_config = test_container.settings.raw.setdefault("models", {}).setdefault(
        "embedding", {}
    )
    embedding_config.update({"batch_size": 16, "timeout_seconds": 30})
    original = test_container.service.embedding_space(context)

    embedding_config["batch_size"] = 1
    embedding_config["timeout_seconds"] = 999
    changed = test_container.service.embedding_space(context)

    assert changed.id == original.id
    assert changed.name == original.name


def test_explicit_hybrid_search_and_reranking_use_shadow_space(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    (source_root / "허가.txt").write_text("계약 조건의 수정을 허가한다.", encoding="utf-8")
    context = test_container.service.request_context()
    test_container.service.sync_filesystem(context, "fixture")
    embedding = FixtureEmbedding()
    reranker = FixtureReranker()
    test_container.service.embedding = embedding
    test_container.service.reranker = reranker
    rebuild = test_container.service.rebuild_semantic_projection(context)
    verification = test_container.service.verify_semantic_projection(context)

    hybrid = test_container.service.search(
        context,
        SearchRequest(query="승인", limit=10),
        mode="hybrid",
    )
    reranked = test_container.service.search(
        context,
        SearchRequest(query="승인", limit=10),
        mode="reranked",
    )

    assert rebuild["indexed_units"] == 2
    assert verification["ok"] is True
    assert verification["indexed_units"] == 2
    assert verification["status"] == "shadow"
    assert {channel for hit in hybrid for channel in hit.metadata["retrieval_channels"]} == {
        "lexical",
        "vector",
    }
    assert reranked[0].title.startswith("허가")
    assert reranked[0].metadata["rerank_rank"] == 1
    assert reranker.document_counts == [2]


def test_reranking_fetches_candidate_units_in_one_repository_call(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    (source_root / "허가.txt").write_text("계약 조건의 수정을 허가한다.", encoding="utf-8")
    repository = CountingMemoryRepository()
    container = build_container(test_container.settings, repository=repository)
    context = container.service.request_context()
    container.service.sync_filesystem(context, "fixture")
    container.service.embedding = FixtureEmbedding()
    container.service.reranker = FixtureReranker()
    container.service.rebuild_semantic_projection(context)

    container.service.search(
        context,
        SearchRequest(query="승인", limit=10),
        mode="reranked",
    )

    assert repository.bulk_lookup_count == 1
    assert repository.single_lookup_count == 0


def test_optional_default_mode_falls_back_but_explicit_hybrid_fails(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    context = test_container.service.request_context()
    test_container.service.sync_filesystem(context, "fixture")
    embedding = FailingEmbedding()
    test_container.service.embedding = embedding
    test_container.settings.raw["search"]["semantic_enabled"] = True
    space = test_container.service.embedding_space(context)
    test_container.repository.save_embedding_space(context, space)
    test_container.repository.activate_embedding_space(context, space.id)

    fallback = test_container.service.search(context, SearchRequest(query="승인"))

    assert fallback
    assert fallback[0].metadata["semantic_degraded"] is True
    with pytest.raises(DependencyUnavailableError):
        test_container.service.search(
            context,
            SearchRequest(query="승인"),
            mode="hybrid",
        )


def test_failed_rebuild_does_not_replace_active_space(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    context = test_container.service.request_context()
    test_container.service.sync_filesystem(context, "fixture")
    good = FixtureEmbedding()
    test_container.service.embedding = good
    built = test_container.service.rebuild_semantic_projection(context)
    test_container.repository.activate_embedding_space(context, built["space_id"])
    test_container.service.embedding = FailingEmbedding()

    with pytest.raises(DependencyUnavailableError):
        test_container.service.rebuild_semantic_projection(context)

    active = test_container.repository.active_embedding_space(context)
    assert active is not None
    assert active.id == built["space_id"]
