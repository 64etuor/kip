from __future__ import annotations

from kip.domain.models import ContextRequest, SearchRequest


def test_sync_search_context_and_stale_detection(test_container):
    source = test_container.settings.filesystem_source("fixture")
    path = test_container.settings.project_root / "source" / "협약변경_승인.txt"
    path.write_text("A과제 참여율 변경은 2026년 7월 1일부터 승인한다.", encoding="utf-8")
    context = test_container.application.operations.request_context()

    summary = test_container.application.ingestion.sync_filesystem(context, "fixture")
    assert summary.inserted == 1
    assert summary.failed == 0

    hits = test_container.application.retrieval.search(context, SearchRequest(query="참여율 변경 승인", limit=10))
    assert hits
    assert "참여율" in hits[0].snippet

    bundle = test_container.application.retrieval.context_bundle(
        context,
        ContextRequest(query="참여율 변경 승인", limit=5, max_chars=10000),
    )
    assert bundle.items
    assert bundle.items[0].source_changed_since_index is False

    unit_id = bundle.items[0].hit.unit_id
    evidence = test_container.application.evidence.read_unit(context, unit_id)
    assert evidence.unit.locator.type == "text_line_range"
    assert evidence.source_changed_since_index is False

    path.write_text("원본이 변경되었다.", encoding="utf-8")
    stale = test_container.application.evidence.read_unit(context, unit_id)
    assert stale.source_changed_since_index is True
