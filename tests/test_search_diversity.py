from __future__ import annotations

from kip.domain.models import SearchRequest


def _seed_same_document_twice(test_container) -> None:
    # 검색본/열람본 suffixes collapse to one logical document with two
    # source objects, giving two hits that share a document_id.
    source = test_container.settings.project_root / "source"
    (source / "결재지침_검색본.txt").write_text(
        "전자결재 위임 규정은 부서장 전결로 한다.", encoding="utf-8"
    )
    (source / "결재지침_열람본.txt").write_text(
        "전자결재 위임 규정은 부서장 전결로 한다. 열람용 사본.", encoding="utf-8"
    )
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")


def test_per_document_cap_promotes_the_second_document(test_container):
    _seed_same_document_twice(test_container)
    source = test_container.settings.project_root / "source"
    (source / "관련안내.txt").write_text(
        "전자결재 시스템 점검 안내문이다.", encoding="utf-8"
    )
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    test_container.settings.raw["search"]["max_hits_per_document"] = 1

    hits = test_container.application.retrieval.search(
        context,
        SearchRequest(query="전자결재", limit=2),
    )

    assert len(hits) == 2
    assert len({hit.document_id for hit in hits}) == 2


def test_backfill_keeps_results_when_only_one_document_matches(test_container):
    _seed_same_document_twice(test_container)
    context = test_container.application.operations.request_context()
    test_container.settings.raw["search"]["max_hits_per_document"] = 1

    hits = test_container.application.retrieval.search(
        context,
        SearchRequest(query="부서장 전결", limit=5),
    )

    # Only one logical document matches; the cap must not shrink results.
    assert len(hits) == 2
    assert len({hit.document_id for hit in hits}) == 1


def test_cap_zero_disables_diversity(test_container):
    _seed_same_document_twice(test_container)
    context = test_container.application.operations.request_context()
    test_container.settings.raw["search"]["max_hits_per_document"] = 0

    hits = test_container.application.retrieval.search(
        context,
        SearchRequest(query="부서장 전결", limit=5),
    )

    assert len(hits) == 2
