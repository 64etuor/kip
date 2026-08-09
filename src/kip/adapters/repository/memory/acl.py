from __future__ import annotations

from datetime import UTC, datetime

from kip.adapters.repository.memory.state import MemoryState
from kip.domain.models import ApprovedAssertion, ContentUnit, RequestContext


def unit_is_visible(
    state: MemoryState,
    unit: ContentUnit,
    context: RequestContext,
    *,
    now: datetime | None = None,
) -> bool:
    if unit.acl_scopes and not set(unit.acl_scopes).issubset(context.acl_scopes):
        return False
    if unit.acl_snapshot_id is None:
        return True
    snapshot = state.acl_snapshots.get(unit.acl_snapshot_id)
    return bool(snapshot and snapshot.is_fresh(now or datetime.now(UTC)))


def assertion_is_visible(
    state: MemoryState,
    assertion: ApprovedAssertion,
    context: RequestContext,
) -> bool:
    if assertion.acl_scopes and not set(assertion.acl_scopes).issubset(
        context.acl_scopes
    ):
        return False
    return all(
        (unit := state.units.get(unit_id)) is not None
        and unit_is_visible(state, unit, context)
        for unit_id in assertion.evidence_unit_ids
    )
