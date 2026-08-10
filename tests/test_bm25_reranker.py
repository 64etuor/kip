from __future__ import annotations

import pytest

from kip.adapters.rerankers import Bm25RerankerAdapter
from kip.container import build_container
from kip.domain.models import SearchRequest
from kip.errors import ConfigurationError


def test_term_frequency_ranks_focused_document_first() -> None:
    reranker = Bm25RerankerAdapter()
    documents = [
        "협력업체 일반 공지사항과 회의 일정 안내",
        "협력업체 평가 등급은 평가 점수에 따라 구분한다. 평가 절차는 연 1회 수행한다.",
        "사내 식당 운영 안내",
    ]

    scores = reranker.rerank("협력업체 평가 등급", documents)

    assert scores[0].index == 1
    assert scores[-1].index == 2


def test_idf_downweights_terms_shared_by_every_candidate() -> None:
    reranker = Bm25RerankerAdapter()
    # 'QMS' appears everywhere; only one candidate carries the rare term.
    documents = [
        "QMS QMS QMS QMS QMS 일반 지침",
        "QMS 리스크 관리대장",
        "QMS 개요",
    ]

    scores = reranker.rerank("QMS 리스크", documents)

    assert scores[0].index == 1


def test_length_normalization_prefers_the_denser_document() -> None:
    reranker = Bm25RerankerAdapter(b=0.75)
    dense = "예산 총액 안내"
    diluted = "예산 총액 안내 " + "무관한 내용 " * 200

    scores = reranker.rerank("예산 총액", [diluted, dense])

    assert scores[0].index == 1


def test_no_match_scores_zero_and_ties_break_by_index() -> None:
    scores = Bm25RerankerAdapter().rerank("존재하지않는어휘", ["가나다", "라마바"])

    assert [score.index for score in scores] == [0, 1]
    assert all(score.score == 0.0 for score in scores)
    assert Bm25RerankerAdapter().rerank("아무거나", []) == []


def test_configuration_bounds_are_enforced() -> None:
    with pytest.raises(ConfigurationError):
        Bm25RerankerAdapter(k1=0.0)
    with pytest.raises(ConfigurationError):
        Bm25RerankerAdapter(b=1.5)
    with pytest.raises(ConfigurationError):
        Bm25RerankerAdapter(max_document_chars=10)


def test_container_builds_bm25_backend_and_search_uses_it(
    test_container,
    tmp_path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "평가지침.txt").write_text(
        "협력업체 평가 등급은 평가 점수에 따라 구분한다.", encoding="utf-8"
    )
    (source_root / "일반공지.txt").write_text(
        "협력업체 대상 주차장 이용 공지", encoding="utf-8"
    )
    test_container.settings.raw["search"].update(
        {
            "lexical_rerank_enabled": True,
            "lexical_rerank_candidate_limit": 10,
        }
    )
    test_container.settings.raw.setdefault("models", {})["reranker"] = {
        "enabled": True,
        "backend": "bm25",
    }
    container = build_container(
        test_container.settings,
        repository=test_container.repository,
    )
    assert isinstance(container.reranker, Bm25RerankerAdapter)
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")

    hits = container.application.retrieval.search(
        context,
        SearchRequest(query="협력업체 평가 등급", limit=5),
    )

    assert hits[0].title.startswith("평가지침")
    assert hits[0].metadata["rerank_rank"] == 1
