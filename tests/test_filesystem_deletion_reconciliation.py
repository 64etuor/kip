from __future__ import annotations

import shutil

import pytest

from kip.domain.knowledge import KnowledgeEntity
from kip.domain.models import ApprovedAssertion, ContextRequest, SearchRequest
from kip.errors import NotFoundError, SourceUnavailableError

QUERY = "정산 증빙 제출기한"


def _write_keeper(container) -> None:
    keeper = container.settings.project_root / "source" / "잔류문서.txt"
    keeper.write_text("이 문서는 계속 보관한다.", encoding="utf-8")


def _write_target(container):
    target = container.settings.project_root / "source" / "삭제대상.txt"
    target.write_text("정산 증빙 제출기한은 2026년 8월 15일이다.", encoding="utf-8")
    return target


def _sync(container):
    context = container.application.operations.request_context()
    return context, container.application.ingestion.sync_filesystem(context, "fixture")


def _search(container, context):
    return container.application.retrieval.search(
        context, SearchRequest(query=QUERY, limit=10)
    )


def test_absent_file_is_tombstoned_only_after_grace_scans(test_container):
    _write_keeper(test_container)
    target = _write_target(test_container)
    context, first = _sync(test_container)
    assert first.inserted == 2
    hits = _search(test_container, context)
    assert hits
    unit_id = hits[0].unit_id
    original_revisions = set(test_container.repository.state.packets_by_revision)

    target.unlink()

    _, second = _sync(test_container)
    assert second.absent == 1
    assert second.tombstoned == 0
    # Still indexed during the grace window.
    assert _search(test_container, context)

    _, third = _sync(test_container)
    assert third.absent == 1
    assert third.tombstoned == 1

    assert _search(test_container, context) == []
    with pytest.raises(NotFoundError):
        test_container.application.evidence.read_unit(context, unit_id)
    # Soft tombstone: the pre-deletion revision history is preserved.
    state = test_container.repository.state
    assert original_revisions.issubset(set(state.packets_by_revision))
    tombstone = state.packets_by_revision[
        next(iter(set(state.current_revision_by_object.values()) - original_revisions))
    ]
    assert tombstone.revision.is_tombstone is True
    assert tombstone.units == []


def test_reappearing_file_clears_absence_mark(test_container):
    _write_keeper(test_container)
    target = _write_target(test_container)
    content = target.read_text(encoding="utf-8")
    context, _ = _sync(test_container)

    target.unlink()
    _, second = _sync(test_container)
    assert second.absent == 1

    target.write_text(content, encoding="utf-8")
    _, third = _sync(test_container)
    assert third.absent == 0
    assert test_container.repository.state.absent_scan_counts == {}

    # The grace counter restarts from zero after reappearance.
    target.unlink()
    _, fourth = _sync(test_container)
    assert fourth.absent == 1
    assert fourth.tombstoned == 0
    assert _search(test_container, context)


def test_failed_scan_never_marks_absence(test_container):
    _write_keeper(test_container)
    _write_target(test_container)
    context, _ = _sync(test_container)

    source_root = test_container.settings.project_root / "source"
    hidden = test_container.settings.project_root / "source-offline"
    shutil.move(source_root, hidden)
    try:
        with pytest.raises(SourceUnavailableError):
            _sync(test_container)
    finally:
        shutil.move(hidden, source_root)

    assert test_container.repository.state.absent_scan_counts == {}
    assert _search(test_container, context)


def test_empty_scan_is_not_interpreted_as_mass_deletion(test_container):
    target = _write_target(test_container)
    context, _ = _sync(test_container)

    target.unlink()
    for _ in range(3):
        _, summary = _sync(test_container)
        assert summary.absent == 0
        assert summary.tombstoned == 0
        assert any("deletion reconciliation" in warning for warning in summary.warnings)
    assert _search(test_container, context)


def test_dry_run_scan_does_not_touch_absence_state(test_container):
    _write_keeper(test_container)
    target = _write_target(test_container)
    context, _ = _sync(test_container)

    target.unlink()
    summary = test_container.application.ingestion.sync_filesystem(
        context, "fixture", dry_run=True
    )
    assert summary.absent == 0
    assert summary.tombstoned == 0
    assert test_container.repository.state.absent_scan_counts == {}


def test_file_grown_past_size_cap_is_not_tombstoned(test_container):
    _write_keeper(test_container)
    target = _write_target(test_container)
    # Shrink the cap below the target's current size after the first sync
    # so subsequent scans see it as "grown too big", not "gone".
    test_container.settings.raw["security"] = {
        "max_file_bytes": target.stat().st_size + 20
    }
    context, first = _sync(test_container)
    assert first.inserted == 2
    unit_id = _search(test_container, context)[0].unit_id

    target.write_text(
        "정산 증빙 제출기한은 2026년 8월 15일이다. " + ("여백 " * 200),
        encoding="utf-8",
    )

    for _ in range(3):
        _, summary = _sync(test_container)
        assert summary.absent == 0
        assert summary.tombstoned == 0
        assert any(
            "삭제대상.txt" in warning and "skipped" in warning
            for warning in summary.warnings
        )

    # The prior revision must remain active: still searchable and readable.
    hits = _search(test_container, context)
    assert any(hit.unit_id == unit_id for hit in hits)
    test_container.application.evidence.read_unit(context, unit_id)


def test_genuinely_deleted_file_is_still_tombstoned_with_a_size_cap_configured(
    test_container,
):
    # Regression guard: fixing the oversize false-tombstone must not weaken
    # the existing deletion-grace behavior for files that are truly gone.
    _write_keeper(test_container)
    target = _write_target(test_container)
    test_container.settings.raw["security"] = {"max_file_bytes": 10_000_000}
    context, first = _sync(test_container)
    assert first.inserted == 2

    target.unlink()
    _, second = _sync(test_container)
    assert second.absent == 1
    assert second.tombstoned == 0
    assert _search(test_container, context)

    _, third = _sync(test_container)
    assert third.absent == 1
    assert third.tombstoned == 1
    assert _search(test_container, context) == []


def test_tombstoned_unit_leaves_context_and_ontology_evidence(test_container):
    _write_keeper(test_container)
    target = _write_target(test_container)
    context, _ = _sync(test_container)
    hits = _search(test_container, context)
    unit_id = hits[0].unit_id

    state = test_container.repository.state
    entity = KnowledgeEntity(
        id="ent_settlement",
        entity_type="Project",
        canonical_name="정산 증빙",
        acl_scopes=["workspace:default"],
    )
    state.entities[entity.id] = entity
    state.assertions["ast_deadline"] = ApprovedAssertion(
        id="ast_deadline",
        subject_id=entity.id,
        predicate="has_deadline",
        object_value={"value": "2026-08-15"},
        ontology_version="core/1.0.0",
        acl_scopes=["workspace:default"],
        evidence_unit_ids=[unit_id],
    )

    fresh = test_container.application.ontology_context.build(context, QUERY)
    assert fresh.had_stale_evidence is False
    assert fresh.context is not None

    target.unlink()
    _sync(test_container)
    _, final = _sync(test_container)
    assert final.tombstoned == 1

    bundle = test_container.application.retrieval.context_bundle(
        context, ContextRequest(query=QUERY, limit=5, max_chars=10000)
    )
    assert all(item.hit.unit_id != unit_id for item in bundle.items)

    # The assertion whose only evidence unit was tombstoned is excluded from
    # ontology evidence at the visibility layer (fail closed, before
    # traversal), so no edge and no evidence surface for the query.
    after = test_container.application.ontology_context.build(context, QUERY)
    assert after.context is None
    assert after.evidence == ()
    # The approved assertion itself is preserved; only its evidence is
    # excluded until it is re-supported or reviewed.
    assert "ast_deadline" in state.assertions
