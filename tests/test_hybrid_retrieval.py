from __future__ import annotations

from pathlib import Path

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.repository.memory.retrieval import MemoryRetrievalStore
from kip.adapters.repository.memory.state import MemoryState
from kip.container import build_container
from kip.domain.models import SearchRequest
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
        return [[1.0, 0.0, 0.0] if "허가" in text else [0.0, 1.0, 0.0] for text in texts]


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


class CountingMemoryRetrievalStore(MemoryRetrievalStore):
    def __init__(self, state: MemoryState) -> None:
        super().__init__(state)
        object.__setattr__(self, "bulk_lookup_count", 0)
        object.__setattr__(self, "lexical_search_count", 0)

    def search(self, context, request, lexemes):
        object.__setattr__(
            self,
            "lexical_search_count",
            self.lexical_search_count + 1,
        )
        return super().search(context, request, lexemes)

    def get_content_units(self, context, unit_ids):
        object.__setattr__(
            self,
            "bulk_lookup_count",
            self.bulk_lookup_count + 1,
        )
        return super().get_content_units(context, unit_ids)


def test_default_lexical_mode_makes_no_model_call(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    embedding = FixtureEmbedding()
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=embedding,
    )
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")

    hits = container.application.retrieval.search(context, SearchRequest(query="승인"))

    assert hits
    assert embedding.query_calls == 0


def test_local_reranker_can_improve_lexical_mode_without_vector_search(
    test_container,
    tmp_path: Path,
) -> None:
    # Given lexical candidates, a local reranker, and no semantic projection.
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    (source_root / "허가.txt").write_text("계약 변경 조건을 허가한다.", encoding="utf-8")
    embedding = FixtureEmbedding()
    reranker = FixtureReranker()
    test_container.settings.raw["search"].update(
        {
            "lexical_rerank_enabled": True,
            "lexical_rerank_candidate_limit": 10,
        }
    )
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=embedding,
        reranker=reranker,
    )
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")

    # When default lexical search runs.
    hits = container.application.retrieval.search(
        context,
        SearchRequest(query="변경", limit=10),
    )

    # Then reranking happens after ACL-filtered lexical retrieval without embeddings.
    assert hits[0].title.startswith("허가")
    assert hits[0].metadata["retrieval_channels"] == ["lexical"]
    assert hits[0].metadata["rerank_rank"] == 1
    assert reranker.document_counts == [2]
    assert embedding.query_calls == 0


def test_explicit_hybrid_search_and_reranking_use_shadow_space(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    (source_root / "허가.txt").write_text("계약 조건의 수정을 허가한다.", encoding="utf-8")
    embedding = FixtureEmbedding()
    reranker = FixtureReranker()
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=embedding,
        reranker=reranker,
    )
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    rebuild = container.application.retrieval.rebuild_semantic_projection(context)
    verification = container.application.retrieval.verify_semantic_projection(context)

    hybrid = container.application.retrieval.search(
        context,
        SearchRequest(query="승인", limit=10),
        mode="hybrid",
    )
    reranked = container.application.retrieval.search(
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


def test_reranked_mode_appends_unreranked_tail_up_to_request_limit(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    (source_root / "허가.txt").write_text("계약 조건의 수정을 허가한다.", encoding="utf-8")
    reranker = FixtureReranker()
    test_container.settings.raw["search"]["rerank_candidate_limit"] = 1
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=FixtureEmbedding(),
        reranker=reranker,
    )
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    container.application.retrieval.rebuild_semantic_projection(context)

    hits = container.application.retrieval.search(
        context,
        SearchRequest(query="승인", limit=10),
        mode="reranked",
    )

    # The rerank depth bounds rerank cost, not the number of returned results.
    assert len(hits) == 2
    assert hits[0].metadata["rerank_rank"] == 1
    assert "rerank_rank" not in hits[1].metadata
    assert reranker.document_counts == [1]


def test_reranking_fetches_candidate_units_in_one_repository_call(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    (source_root / "허가.txt").write_text("계약 조건의 수정을 허가한다.", encoding="utf-8")
    state = MemoryState()
    counting_retrieval = CountingMemoryRetrievalStore(state)
    repository = MemoryRepository(state, retrieval=counting_retrieval)
    container = build_container(
        test_container.settings,
        repository=repository,
        embedding=FixtureEmbedding(),
        reranker=FixtureReranker(),
    )
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    container.application.retrieval.rebuild_semantic_projection(context)

    container.application.retrieval.search(
        context,
        SearchRequest(query="승인", limit=10),
        mode="reranked",
    )

    assert counting_retrieval.bulk_lookup_count == 1


def test_vector_only_mode_does_not_pay_for_lexical_candidate_retrieval(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    state = MemoryState()
    counting_retrieval = CountingMemoryRetrievalStore(state)
    repository = MemoryRepository(state, retrieval=counting_retrieval)
    container = build_container(
        test_container.settings,
        repository=repository,
        embedding=FixtureEmbedding(),
    )
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    container.application.retrieval.rebuild_semantic_projection(context)

    hits = container.application.retrieval.search(
        context,
        SearchRequest(query="승인", limit=10),
        mode="vector",
    )

    assert hits
    assert counting_retrieval.lexical_search_count == 0


def test_optional_default_mode_falls_back_but_explicit_hybrid_fails(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "승인.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    embedding = FailingEmbedding()
    test_container.settings.raw["search"]["semantic_enabled"] = True
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
        embedding=embedding,
    )
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    space = container.application.retrieval.embedding_space(context)
    container.repository.retrieval.save_embedding_space(context, space)
    container.repository.retrieval.activate_embedding_space(context, space.id)

    fallback = container.application.retrieval.search(context, SearchRequest(query="승인"))

    assert fallback
    assert fallback[0].metadata["semantic_degraded"] is True
    with pytest.raises(DependencyUnavailableError):
        container.application.retrieval.search(
            context,
            SearchRequest(query="승인"),
            mode="hybrid",
        )
