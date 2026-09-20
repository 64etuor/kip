"""Behaviour parity for `InteractionStore` and `QueryTraceStore`.

`test_repository_behavior_parity.py` covers ingestion, retrieval, evidence,
knowledge, jobs and operations. The two ports exercised here were the
remaining pair with a memory and a PostgreSQL adapter and no test that ran
the same scenario against both, which is how three divergences survived:
the memory interaction store enforced an `admin` role the PostgreSQL one
never asked for, and the memory query trace store neither bounded `limit`
nor ordered its result the way the SQL `ORDER BY` does.

Every scenario below runs identically against whichever backend the
`harness` fixture parametrized, so a divergence shows up as one backend
failing while the other passes. Values a store generates (IDs, and the
timestamps a backend takes from the clock) are either supplied by the test
or normalized out of the comparison.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.repository.postgres import PostgresRepository
from kip.domain.interactions import (
    ClarificationAnswer,
    ClarificationChoice,
    ClarificationQuestion,
    DiscoveryStatus,
    FeedbackSubmission,
    OntologyDiscoveryCandidate,
    OntologyDiscoveryReview,
    UserPreferenceWrite,
)
from kip.domain.models import RequestContext
from kip.domain.telemetry import QueryTrace
from kip.errors import ConflictError, NotFoundError, ValidationError
from kip.ids import new_id
from kip.ports.interactions import InteractionStore
from kip.ports.telemetry import QueryTraceStore

# Same env-guarded pattern as tests/contract/test_repository_behavior_parity.py:
# skip the postgres side cleanly when no integration database is configured,
# while the memory side always runs.
from tests.environment import TEST_DATABASE_SKIP_REASON, TEST_POSTGRES_URL

URL = TEST_POSTGRES_URL
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class Harness:
    """A repository's two stores plus the workspaces the test may write to."""

    interactions: InteractionStore
    telemetry: QueryTraceStore
    workspace: str
    other_workspace: str

    def context(
        self,
        *,
        principal_id: str = "principal_parity",
        workspace: str | None = None,
        roles: Sequence[str] = (),
    ) -> RequestContext:
        selected = workspace or self.workspace
        return RequestContext(
            workspace=selected,
            principal_id=principal_id,
            acl_scopes=[f"workspace:{selected}"],
            roles=list(roles),
            request_id=new_id("req"),
        )


@pytest.fixture(params=["memory", "postgres"])
def harness(request: pytest.FixtureRequest) -> Iterator[Harness]:
    if request.param == "memory":
        repository = MemoryRepository()
        yield Harness(
            interactions=repository.interactions,
            telemetry=repository.telemetry,
            workspace="parity_memory",
            other_workspace="parity_memory_other",
        )
        return

    if not URL:
        pytest.skip(TEST_DATABASE_SKIP_REASON)
    pytest.importorskip("psycopg")
    token = uuid.uuid4().hex[:12]
    workspace = f"test_{token}"
    other_workspace = f"test_{token}_other"
    repository = PostgresRepository(str(URL))
    repository.operations.migrate(MIGRATIONS_DIR)
    try:
        yield Harness(
            interactions=repository.interactions,
            telemetry=repository.telemetry,
            workspace=workspace,
            other_workspace=other_workspace,
        )
    finally:
        import psycopg

        with (
            psycopg.connect(str(URL), autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM kip.workspaces WHERE slug = ANY(%s)",
                ([workspace, other_workspace],),
            )


def _question(
    *,
    question_id: str | None = None,
    expires_at: datetime = LATER,
    preference_key: str | None = "preferred_region",
) -> ClarificationQuestion:
    return ClarificationQuestion(
        id=question_id or new_id("clrq"),
        reason="scope_selection",
        prompt="Which region should the answer cover?",
        choices=[
            ClarificationChoice(id="emea", label="EMEA"),
            ClarificationChoice(id="apac", label="APAC"),
        ],
        allow_freeform=True,
        allow_multiple=False,
        preference_key=preference_key,
        created_at=NOW,
        expires_at=expires_at,
    )


def _candidate(
    *,
    fingerprint: str,
    symbol: str = "funds",
    label: str = "funds",
    definition: str = "One organization funds a project.",
) -> OntologyDiscoveryCandidate:
    return OntologyDiscoveryCandidate(
        id=new_id("odc"),
        domain_profile="empty",
        kind="predicate",
        symbol=symbol,
        label=label,
        definition=definition,
        domain=["Organization"],
        range=["Project"],
        inverse="funded_by",
        risk="low",
        review="conditional",
        extraction="mixed",
        fingerprint=fingerprint,
        created_at=NOW,
        updated_at=NOW,
    )


def _trace(*, trace_id: str, request_id: str | None, started_at: datetime) -> QueryTrace:
    return QueryTrace(
        id=trace_id,
        request_id=request_id,
        route="search",
        outcome="succeeded",
        started_at=started_at,
        duration_ms=12.5,
    )


# -- clarifications ---------------------------------------------------------


def test_clarification_create_and_read_round_trip(harness: Harness) -> None:
    context = harness.context()
    question = _question()

    created = harness.interactions.create_clarification(context, question)
    assert created == question

    read = harness.interactions.get_clarification(context, question.id, now=NOW)
    assert read == question


def test_clarification_create_refuses_a_question_that_is_not_open(
    harness: Harness,
) -> None:
    context = harness.context()
    question = _question().model_copy(update={"status": "answered"})

    with pytest.raises(ValidationError, match="new clarification must be open"):
        harness.interactions.create_clarification(context, question)


def test_clarification_create_refuses_a_duplicate_id(harness: Harness) -> None:
    context = harness.context()
    question = _question()
    harness.interactions.create_clarification(context, question)

    with pytest.raises(ConflictError, match="clarification already exists"):
        harness.interactions.create_clarification(context, question)


def test_clarification_is_readable_only_by_the_principal_that_asked(
    harness: Harness,
) -> None:
    question = _question()
    harness.interactions.create_clarification(harness.context(), question)
    other = harness.context(principal_id="principal_other")

    with pytest.raises(NotFoundError, match="clarification not found"):
        harness.interactions.get_clarification(other, question.id, now=NOW)
    with pytest.raises(NotFoundError, match="clarification not found"):
        harness.interactions.get_clarification(
            harness.context(), "clrq_missing", now=NOW
        )


def test_clarification_reads_as_expired_once_its_deadline_passes(
    harness: Harness,
) -> None:
    context = harness.context()
    question = _question(expires_at=NOW + timedelta(minutes=1))
    harness.interactions.create_clarification(context, question)

    after = harness.interactions.get_clarification(
        context, question.id, now=NOW + timedelta(minutes=5)
    )
    assert after.status == "expired"

    with pytest.raises(ConflictError, match="clarification has expired"):
        harness.interactions.answer_clarification(
            context,
            ClarificationAnswer(question_id=question.id, option_ids=["emea"]),
            now=NOW + timedelta(minutes=5),
        )


def test_answering_a_clarification_resolves_it_once(harness: Harness) -> None:
    context = harness.context()
    question = _question()
    harness.interactions.create_clarification(context, question)

    resolution = harness.interactions.answer_clarification(
        context,
        ClarificationAnswer(
            question_id=question.id,
            option_ids=["apac"],
            freeform="and Japan",
            remember=True,
        ),
        now=NOW,
    )

    assert resolution.question.status == "answered"
    assert resolution.selected_values == ["apac", "and Japan"]
    assert resolution.preference is not None
    # The preference ID is generated by the store; everything else about it is
    # decided by the answer and the supplied clock.
    assert resolution.preference.model_dump(exclude={"id"}) == {
        "schema_version": "kip.user-preference.v1",
        "key": "preferred_region",
        "values": ["apac", "and Japan"],
        "created_at": NOW,
        "updated_at": NOW,
    }
    assert (
        harness.interactions.get_clarification(context, question.id, now=NOW).status
        == "answered"
    )

    with pytest.raises(ConflictError, match="already been answered"):
        harness.interactions.answer_clarification(
            context,
            ClarificationAnswer(question_id=question.id, option_ids=["emea"]),
            now=NOW,
        )


def test_answering_refuses_to_remember_a_question_without_a_preference_key(
    harness: Harness,
) -> None:
    context = harness.context()
    question = _question(preference_key=None)
    harness.interactions.create_clarification(context, question)

    with pytest.raises(ValidationError, match="does not permit remembered preferences"):
        harness.interactions.answer_clarification(
            context,
            ClarificationAnswer(
                question_id=question.id, option_ids=["emea"], remember=True
            ),
            now=NOW,
        )


def test_expired_clarifications_are_pruned_by_deadline_and_workspace(
    harness: Harness,
) -> None:
    # Authorization for this operation belongs to `InteractionUseCases`
    # (`prune_expired_clarifications` requires the admin role); neither store
    # re-checks it, so the parity case runs with an unprivileged context.
    context = harness.context()
    stale = _question(expires_at=NOW + timedelta(minutes=1))
    fresh = _question(expires_at=NOW + timedelta(days=1))
    harness.interactions.create_clarification(context, stale)
    harness.interactions.create_clarification(context, fresh)
    elsewhere = harness.context(workspace=harness.other_workspace)
    harness.interactions.create_clarification(
        elsewhere, _question(expires_at=NOW + timedelta(minutes=1))
    )

    deleted = harness.interactions.delete_expired_clarifications(
        context, before=NOW + timedelta(hours=2)
    )

    assert deleted == 1
    assert (
        harness.interactions.get_clarification(context, fresh.id, now=NOW).id == fresh.id
    )
    with pytest.raises(NotFoundError):
        harness.interactions.get_clarification(context, stale.id, now=NOW)


# -- preferences ------------------------------------------------------------


def test_preferences_upsert_list_and_delete(harness: Harness) -> None:
    context = harness.context()

    stored = harness.interactions.upsert_preference(
        context,
        UserPreferenceWrite(key="preferred_region", values=["emea"], confirmed=True),
        now=NOW,
    )
    harness.interactions.upsert_preference(
        context,
        UserPreferenceWrite(key="answer_language", values=["ko"], confirmed=True),
        now=NOW,
    )

    # A second write to the same key keeps the record's identity and creation
    # time and replaces only its values.
    updated = harness.interactions.upsert_preference(
        context,
        UserPreferenceWrite(
            key="preferred_region", values=["emea", "apac"], confirmed=True
        ),
        now=LATER,
    )
    assert (updated.id, updated.created_at) == (stored.id, NOW)
    assert (updated.values, updated.updated_at) == (["emea", "apac"], LATER)

    listed = harness.interactions.list_preferences(context)
    assert [(item.key, item.values) for item in listed] == [
        ("answer_language", ["ko"]),
        ("preferred_region", ["emea", "apac"]),
    ]

    assert harness.interactions.list_preferences(
        harness.context(principal_id="principal_other")
    ) == []

    assert (
        harness.interactions.delete_preference(context, "preferred_region", now=LATER)
        is True
    )
    assert (
        harness.interactions.delete_preference(context, "preferred_region", now=LATER)
        is False
    )
    assert [item.key for item in harness.interactions.list_preferences(context)] == [
        "answer_language"
    ]


# -- feedback ---------------------------------------------------------------


def test_record_feedback_returns_the_stored_submission(harness: Harness) -> None:
    context = harness.context()
    request_id = new_id("req")

    feedback = harness.interactions.record_feedback(
        context,
        FeedbackSubmission(
            request_id=request_id,
            outcome="not_helpful",
            reason_codes=["missing_evidence"],
        ),
        now=NOW,
    )

    assert feedback.model_dump(exclude={"id"}) == {
        "schema_version": "kip.interaction-feedback.v1",
        "request_id": request_id,
        "outcome": "not_helpful",
        "reason_codes": ["missing_evidence"],
        "created_at": NOW,
    }


# -- ontology discovery review ----------------------------------------------


def test_discovery_candidate_save_is_deduplicated_by_fingerprint(
    harness: Harness,
) -> None:
    context = harness.context()
    first = harness.interactions.save_ontology_discovery_candidate(
        context, _candidate(fingerprint="sha256:parity-dedup")
    )
    assert first.occurrence_count == 1

    # The fingerprint deliberately excludes label and definition, so a
    # re-proposal refreshes them instead of being dropped.
    second = harness.interactions.save_ontology_discovery_candidate(
        context,
        _candidate(
            fingerprint="sha256:parity-dedup",
            label="funds (revised)",
            definition="One organization funds another organization's project.",
        ).model_copy(update={"updated_at": LATER}),
    )
    assert (second.id, second.occurrence_count) == (first.id, 2)
    assert second.label == "funds (revised)"
    assert second.updated_at == LATER
    assert second.created_at == NOW


def test_discovery_candidates_are_listed_newest_first_and_filtered_by_status(
    harness: Harness,
) -> None:
    context = harness.context()
    older = harness.interactions.save_ontology_discovery_candidate(
        context, _candidate(fingerprint="sha256:parity-older", symbol="funds")
    )
    newer = harness.interactions.save_ontology_discovery_candidate(
        context,
        _candidate(fingerprint="sha256:parity-newer", symbol="audits").model_copy(
            update={"updated_at": LATER}
        ),
    )
    assert [
        item.id
        for item in harness.interactions.list_ontology_discovery_candidates(
            context, status=None, limit=100
        )
    ] == [newer.id, older.id]
    assert [
        item.id
        for item in harness.interactions.list_ontology_discovery_candidates(
            context, status=None, limit=1
        )
    ] == [newer.id]

    # A review stamps `updated_at`, which is also the list's sort key, so the
    # reviewed candidate moves to the front of an unfiltered listing.
    harness.interactions.review_ontology_discovery_candidate(
        context,
        older.id,
        OntologyDiscoveryReview(action="reject"),
        now=LATER + timedelta(hours=1),
    )
    assert [
        item.id
        for item in harness.interactions.list_ontology_discovery_candidates(
            context, status=None, limit=100
        )
    ] == [older.id, newer.id]
    assert [
        item.id
        for item in harness.interactions.list_ontology_discovery_candidates(
            context, status="proposed", limit=100
        )
    ] == [newer.id]
    assert [
        item.id
        for item in harness.interactions.list_ontology_discovery_candidates(
            context, status="rejected", limit=100
        )
    ] == [older.id]
    # A candidate in another workspace is never visible here.
    harness.interactions.save_ontology_discovery_candidate(
        harness.context(workspace=harness.other_workspace),
        _candidate(fingerprint="sha256:parity-elsewhere"),
    )
    assert len(
        harness.interactions.list_ontology_discovery_candidates(
            context, status=None, limit=100
        )
    ) == 2


def test_discovery_candidate_review_is_recorded_once(harness: Harness) -> None:
    context = harness.context()
    candidate = harness.interactions.save_ontology_discovery_candidate(
        context, _candidate(fingerprint="sha256:parity-review")
    )

    reviewed = harness.interactions.review_ontology_discovery_candidate(
        context,
        candidate.id,
        OntologyDiscoveryReview(action="accept", note="matches the domain profile"),
        now=LATER,
    )
    assert reviewed.status == "accepted_for_release"
    assert reviewed.reviewed_at == LATER
    assert reviewed.updated_at == LATER
    assert reviewed.reviewed_by == context.principal_id
    assert reviewed.review_note == "matches the domain profile"
    assert (
        harness.interactions.get_ontology_discovery_candidate(context, candidate.id)
        == reviewed
    )

    with pytest.raises(ConflictError, match="already been reviewed"):
        harness.interactions.review_ontology_discovery_candidate(
            context, candidate.id, OntologyDiscoveryReview(action="reject"), now=LATER
        )

    # A reviewed candidate is never reopened or re-counted by a re-proposal.
    again = harness.interactions.save_ontology_discovery_candidate(
        context, _candidate(fingerprint="sha256:parity-review", label="funds (again)")
    )
    assert (again.status, again.occurrence_count, again.label) == (
        "accepted_for_release",
        1,
        "funds",
    )


@pytest.mark.parametrize("status", [None, "proposed"])
def test_unknown_discovery_candidate_is_not_found(
    harness: Harness, status: DiscoveryStatus | None
) -> None:
    context = harness.context()
    assert (
        harness.interactions.list_ontology_discovery_candidates(
            context, status=status, limit=10
        )
        == []
    )
    with pytest.raises(NotFoundError, match="ontology discovery candidate not found"):
        harness.interactions.get_ontology_discovery_candidate(context, "odc_missing")
    with pytest.raises(NotFoundError, match="ontology discovery candidate not found"):
        harness.interactions.review_ontology_discovery_candidate(
            context, "odc_missing", OntologyDiscoveryReview(action="accept"), now=NOW
        )


# -- query traces -----------------------------------------------------------


def test_query_traces_are_listed_newest_first_within_the_workspace(
    harness: Harness,
) -> None:
    context = harness.context()
    request_id = new_id("req")
    older = _trace(trace_id="qtrace_older", request_id=request_id, started_at=NOW)
    newer = _trace(trace_id="qtrace_newer", request_id=None, started_at=LATER)
    harness.telemetry.record(context, older)
    harness.telemetry.record(context, newer)
    harness.telemetry.record(
        harness.context(workspace=harness.other_workspace),
        _trace(trace_id="qtrace_elsewhere", request_id=None, started_at=LATER),
    )

    assert [trace.id for trace in harness.telemetry.list_traces(context)] == [
        "qtrace_newer",
        "qtrace_older",
    ]
    assert [
        trace.id for trace in harness.telemetry.list_traces(context, limit=1)
    ] == ["qtrace_newer"]
    assert [
        trace.id
        for trace in harness.telemetry.list_traces(context, request_id=request_id)
    ] == ["qtrace_older"]
    assert harness.telemetry.list_traces(context, request_id="req_absent") == []
    assert harness.telemetry.list_traces(context)[1] == older


def test_query_traces_with_one_timestamp_are_ordered_by_id(harness: Harness) -> None:
    context = harness.context()
    for trace_id in ("qtrace_b", "qtrace_a", "qtrace_c"):
        harness.telemetry.record(
            context, _trace(trace_id=trace_id, request_id=None, started_at=NOW)
        )

    assert [trace.id for trace in harness.telemetry.list_traces(context)] == [
        "qtrace_c",
        "qtrace_b",
        "qtrace_a",
    ]


@pytest.mark.parametrize("limit", [0, 1001])
def test_query_trace_limit_is_bounded(harness: Harness, limit: int) -> None:
    with pytest.raises(ValidationError, match="between 1 and 1000"):
        harness.telemetry.list_traces(harness.context(), limit=limit)


def test_query_traces_are_pruned_by_start_time_and_workspace(harness: Harness) -> None:
    context = harness.context()
    harness.telemetry.record(
        context, _trace(trace_id="qtrace_stale", request_id=None, started_at=NOW)
    )
    harness.telemetry.record(
        context, _trace(trace_id="qtrace_kept", request_id=None, started_at=LATER)
    )
    elsewhere = harness.context(workspace=harness.other_workspace)
    harness.telemetry.record(
        elsewhere, _trace(trace_id="qtrace_other", request_id=None, started_at=NOW)
    )

    deleted = harness.telemetry.delete_before(context, NOW + timedelta(minutes=30))

    assert deleted == 1
    assert [trace.id for trace in harness.telemetry.list_traces(context)] == [
        "qtrace_kept"
    ]
    assert [trace.id for trace in harness.telemetry.list_traces(elsewhere)] == [
        "qtrace_other"
    ]
    assert harness.telemetry.delete_before(context, NOW) == 0
