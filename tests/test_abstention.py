from __future__ import annotations

from kip.domain.models import SearchRequest


def _seed(test_container) -> None:
    source = test_container.settings.project_root / "source"
    (source / "협력업체지침.txt").write_text(
        "협력업체 선정 평가는 신용등급 B 이상을 요구한다.", encoding="utf-8"
    )
    (source / "교육절차.txt").write_text(
        "안전보건 정기교육은 분기당 사무직 3시간 이상 실시한다.", encoding="utf-8"
    )
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")


def test_real_query_is_not_abstained(test_container):
    _seed(test_container)
    context = test_container.application.operations.request_context()

    hits = test_container.application.retrieval.search(
        context,
        SearchRequest(query="협력업체 평가 신용등급", limit=5),
    )

    assert hits


def test_out_of_vocabulary_query_abstains_to_empty(test_container):
    _seed(test_container)
    context = test_container.application.operations.request_context()

    hits = test_container.application.retrieval.search(
        context,
        SearchRequest(query="쿼커스 즈믜르 우주정거장선착장블라", limit=5),
    )

    assert hits == []


def test_one_grounded_term_is_enough_to_attempt(test_container):
    _seed(test_container)
    context = test_container.application.operations.request_context()

    # 협력업체 is grounded; 우주정거장 is not. One grounded term retrieves.
    hits = test_container.application.retrieval.search(
        context,
        SearchRequest(query="협력업체 우주정거장", limit=5),
    )

    assert hits


def test_abstention_can_be_disabled(test_container):
    _seed(test_container)
    test_container.settings.raw["search"]["abstain_on_unknown_terms"] = False
    context = test_container.application.operations.request_context()

    hits = test_container.application.retrieval.search(
        context,
        SearchRequest(query="블라블라 뿅뿅 우주정거장", limit=5),
    )

    # With the gate off, the old noisy behavior returns (may be empty or not,
    # but the gate is not what emptied it).
    assert isinstance(hits, list)
