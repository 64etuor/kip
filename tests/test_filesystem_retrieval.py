from __future__ import annotations

import os
import time
from pathlib import Path

from kip.adapters.connectors.filesystem import FileSystemConnector
from kip.domain.models import ContextRequest, SearchRequest


def test_sync_search_context_and_stale_detection(test_container):
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


def test_context_item_cap_keeps_one_big_unit_from_starving_the_bundle(
    test_container,
):
    source = test_container.settings.project_root / "source"
    (source / "큰문서.txt").write_text("정산 기준 안내 " * 2000, encoding="utf-8")
    (source / "작은문서.txt").write_text("정산 기준은 별도 규정을 따른다.", encoding="utf-8")
    test_container.settings.raw["search"]["context_item_max_chars"] = 500
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    # The configured value is a floor; each item may also claim its fair
    # share of the caller's budget (max_chars // limit).
    tight = test_container.application.retrieval.context_bundle(
        context,
        ContextRequest(query="정산 기준", limit=5, max_chars=2000),
    )
    generous = test_container.application.retrieval.context_bundle(
        context,
        ContextRequest(query="정산 기준", limit=5, max_chars=100000),
    )

    assert len(tight.items) >= 2
    assert all(len(item.body) <= 500 for item in tight.items)
    assert tight.truncated is True
    # Raising the budget must actually return longer passages.
    assert max(len(item.body) for item in generous.items) > 500


def test_second_sync_skips_unchanged_files_without_reading_them(test_container):
    path = test_container.settings.project_root / "source" / "불변문서.txt"
    path.write_text("변경되지 않은 원본이다.", encoding="utf-8")
    context = test_container.application.operations.request_context()
    first = test_container.application.ingestion.sync_filesystem(context, "fixture")
    assert first.inserted == 1

    # Make the bytes unreadable while keeping stat identical: the stat fast
    # path must classify the file as unchanged without opening it.
    path.chmod(0o000)
    try:
        second = test_container.application.ingestion.sync_filesystem(
            context, "fixture"
        )
    finally:
        path.chmod(0o644)

    assert second.unchanged == 1
    assert second.failed == 0


def test_changed_stat_still_triggers_full_ingest(test_container):
    path = test_container.settings.project_root / "source" / "수정문서.txt"
    path.write_text("첫번째 내용", encoding="utf-8")
    context = test_container.application.operations.request_context()
    assert (
        test_container.application.ingestion.sync_filesystem(context, "fixture").inserted
        == 1
    )

    path.write_text("두번째 내용으로 바뀌었다", encoding="utf-8")
    summary = test_container.application.ingestion.sync_filesystem(context, "fixture")

    assert summary.replaced == 1
    hits = test_container.application.retrieval.search(
        context, SearchRequest(query="두번째 내용", limit=5)
    )
    assert hits


def test_bulk_reopen_trusts_matching_stat_without_reading_the_file(test_container):
    path = test_container.settings.project_root / "source" / "재열람.txt"
    path.write_text("정산 기준 재열람 근거", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    unit_id = test_container.application.retrieval.search(
        context, SearchRequest(query="재열람")
    )[0].unit_id

    path.chmod(0o000)
    try:
        fast = test_container.application.evidence.read_unit(
            context, unit_id, verify_hash=False
        )
    finally:
        path.chmod(0o644)

    assert fast.source_changed_since_index is False
    assert fast.current_source_sha256 == fast.indexed_source_sha256

    path.write_text("원본이 바뀌었다", encoding="utf-8")
    changed = test_container.application.evidence.read_unit(
        context, unit_id, verify_hash=False
    )
    assert changed.source_changed_since_index is True


def test_settle_window_skips_recently_modified_files(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh.txt"
    fresh.write_text("just written", encoding="utf-8")
    settled = tmp_path / "settled.txt"
    settled.write_text("older file", encoding="utf-8")
    old_ns = time.time_ns() - 60 * 1_000_000_000
    os.utime(settled, ns=(old_ns, old_ns))
    connector = FileSystemConnector(tmp_path, settle_seconds=5.0)

    records = list(connector.scan())

    assert [record.relative_path for record in records] == ["settled.txt"]


def test_scan_defers_content_hashing_until_first_use(tmp_path: Path) -> None:
    path = tmp_path / "지연해시.txt"
    path.write_text("본문", encoding="utf-8")
    connector = FileSystemConnector(tmp_path, settle_seconds=0)

    record = next(iter(connector.scan()))
    path.chmod(0o000)
    try:
        computed_eagerly = True
        try:
            _ = record.sha256
        except PermissionError:
            computed_eagerly = False
    finally:
        path.chmod(0o644)

    assert computed_eagerly is False
    assert len(record.sha256) == 64
