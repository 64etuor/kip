from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from kip.adapters.repository.postgres.database import PostgresDatabase, _json
from kip.domain.interactions import (
    ClarificationAnswer,
    ClarificationQuestion,
    ClarificationResolution,
    DiscoveryStatus,
    FeedbackSubmission,
    InteractionEvent,
    InteractionFeedback,
    OntologyDiscoveryCandidate,
    OntologyDiscoveryReview,
    UserPreference,
    UserPreferenceWrite,
    resolved_clarification_values,
)
from kip.domain.models import RequestContext
from kip.errors import ConflictError, NotFoundError, ValidationError

# `OntologyDiscoveryCandidate` fields that make up the release spec for
# `entity_type` (`parent`) and `predicate` (`domain`, `range`, `inverse`,
# `risk`, `review`, `extraction`) candidates. Stored losslessly as a single
# nullable `proposal_spec` jsonb column rather than one scalar column per
# field; `target_symbol` keeps its own dedicated column for backward
# compatibility and is not part of this spec payload.
_SPEC_FIELDS = ("parent", "domain", "range", "inverse", "risk", "review", "extraction")


def _spec_payload(candidate: OntologyDiscoveryCandidate) -> dict[str, Any] | None:
    payload = {field: getattr(candidate, field) for field in _SPEC_FIELDS}
    if all(value is None for value in payload.values()):
        return None
    return payload


class PostgresInteractionStore:
    name = "postgresql"

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def create_clarification(
        self,
        context: RequestContext,
        question: ClarificationQuestion,
    ) -> ClarificationQuestion:
        if question.status != "open":
            raise ValidationError("new clarification must be open")
        with self._database._connection(context) as connection:
            self._database._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO interaction.clarifications(
                        id, workspace_id, principal_id, reason, prompt, choices,
                        allow_freeform, allow_multiple, preference_key, status,
                        created_at, expires_at
                    ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        question.id,
                        context.workspace,
                        context.principal_id,
                        question.reason,
                        question.prompt,
                        _json(question.choices),
                        question.allow_freeform,
                        question.allow_multiple,
                        question.preference_key,
                        question.status,
                        question.created_at,
                        question.expires_at,
                    ),
                )
            connection.commit()
        return question

    def get_clarification(
        self,
        context: RequestContext,
        question_id: str,
        *,
        now: datetime,
    ) -> ClarificationQuestion:
        with (
            self._database._connection(context) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                SELECT id, reason, prompt, choices, allow_freeform, allow_multiple,
                       preference_key, status, created_at, expires_at
                FROM interaction.clarifications
                WHERE workspace_id=%s AND principal_id=%s AND id=%s
                """,
                (context.workspace, context.principal_id, question_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise NotFoundError("clarification not found")
        question = _question(row)
        if question.status == "open" and question.expires_at <= now:
            return question.model_copy(update={"status": "expired"})
        return question

    def answer_clarification(
        self,
        context: RequestContext,
        answer: ClarificationAnswer,
        *,
        now: datetime,
    ) -> ClarificationResolution:
        with self._database._connection(context) as connection:
            self._database._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, reason, prompt, choices, allow_freeform, allow_multiple,
                           preference_key, status, created_at, expires_at
                    FROM interaction.clarifications
                    WHERE workspace_id=%s AND principal_id=%s AND id=%s
                    FOR UPDATE
                    """,
                    (context.workspace, context.principal_id, answer.question_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise NotFoundError("clarification not found")
                question = _question(row)
                if question.status == "open" and question.expires_at <= now:
                    raise ConflictError("clarification has expired")
                if question.status != "open":
                    raise ConflictError("clarification has already been answered")
                selected_values = resolved_clarification_values(question, answer)
                preference: UserPreference | None = None
                if answer.remember:
                    if question.preference_key is None:
                        raise ValidationError(
                            "clarification does not permit remembered preferences"
                        )
                    preference = self._upsert_preference(
                        cursor,
                        context,
                        UserPreferenceWrite(
                            key=question.preference_key,
                            values=selected_values,
                            confirmed=True,
                        ),
                        now=now,
                    )
                cursor.execute(
                    """
                    UPDATE interaction.clarifications
                    SET status='answered'
                    WHERE workspace_id=%s AND principal_id=%s AND id=%s
                    """,
                    (context.workspace, context.principal_id, question.id),
                )
                answered = question.model_copy(update={"status": "answered"})
                self._record_event(
                    cursor,
                    context,
                    InteractionEvent(
                        kind="clarification_answered",
                        created_at=now,
                        clarification_id=question.id,
                        preference_id=preference.id if preference is not None else None,
                    ),
                )
            connection.commit()
        return ClarificationResolution(
            question=answered,
            selected_values=selected_values,
            preference=preference,
        )

    def list_preferences(
        self,
        context: RequestContext,
    ) -> list[UserPreference]:
        with (
            self._database._connection(context) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                SELECT id, preference_key, values, created_at, updated_at
                FROM interaction.preferences
                WHERE workspace_id=%s AND principal_id=%s
                ORDER BY preference_key ASC, id ASC
                """,
                (context.workspace, context.principal_id),
            )
            return [_preference(row) for row in cursor.fetchall()]

    def upsert_preference(
        self,
        context: RequestContext,
        preference: UserPreferenceWrite,
        *,
        now: datetime,
    ) -> UserPreference:
        with self._database._connection(context) as connection:
            self._database._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                stored = self._upsert_preference(cursor, context, preference, now=now)
            connection.commit()
        return stored

    def delete_preference(
        self,
        context: RequestContext,
        key: str,
        *,
        now: datetime,
    ) -> bool:
        with self._database._connection(context) as connection:
            self._database._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM interaction.preferences
                    WHERE workspace_id=%s AND principal_id=%s AND preference_key=%s
                    RETURNING id
                    """,
                    (context.workspace, context.principal_id, key),
                )
                row = cursor.fetchone()
                if row is not None:
                    self._record_event(
                        cursor,
                        context,
                        InteractionEvent(
                            kind="preference_deleted",
                            created_at=now,
                            preference_id=row["id"],
                        ),
                    )
            connection.commit()
        return row is not None

    def record_feedback(
        self,
        context: RequestContext,
        submission: FeedbackSubmission,
        *,
        now: datetime,
    ) -> InteractionFeedback:
        feedback = InteractionFeedback(
            request_id=submission.request_id,
            outcome=submission.outcome,
            reason_codes=submission.reason_codes,
            created_at=now,
        )
        with self._database._connection(context) as connection:
            self._database._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO interaction.feedback(
                        id, workspace_id, principal_id, request_id, outcome,
                        reason_codes, created_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        feedback.id,
                        context.workspace,
                        context.principal_id,
                        feedback.request_id,
                        feedback.outcome,
                        feedback.reason_codes,
                        feedback.created_at,
                    ),
                )
                self._record_event(
                    cursor,
                    context,
                    InteractionEvent(
                        kind="feedback_submitted",
                        created_at=now,
                        feedback_id=feedback.id,
                        outcome=feedback.outcome,
                    ),
                )
            connection.commit()
        return feedback

    def save_ontology_discovery_candidate(
        self,
        context: RequestContext,
        candidate: OntologyDiscoveryCandidate,
    ) -> OntologyDiscoveryCandidate:
        spec_payload = _spec_payload(candidate)
        with self._database._connection(context) as connection:
            self._database._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                # A re-proposal of the same (fingerprinted) symbol with
                # corrected label/definition/spec must not be silently
                # dropped: the fingerprint intentionally excludes these
                # fields (stable dedup identity), so the conflict path
                # refreshes them here from the incoming row (`EXCLUDED`).
                # The `WHERE status='proposed'` clause makes this refresh
                # conditional at the row level: if the existing row has
                # already been reviewed, the `DO UPDATE` predicate is false,
                # no column is touched, and the `RETURNING` clause below
                # yields no row, falling through to the plain `SELECT`.
                cursor.execute(
                    """
                    INSERT INTO knowledge.ontology_discovery_candidates(
                        id, workspace_id, submitted_by, domain_profile, kind, symbol,
                        label, definition, target_symbol, proposal_spec, fingerprint,
                        status, occurrence_count, created_at, updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
                    ON CONFLICT (workspace_id, fingerprint) DO UPDATE
                    SET occurrence_count=knowledge.ontology_discovery_candidates.occurrence_count + 1,
                        updated_at=EXCLUDED.updated_at,
                        label=EXCLUDED.label,
                        definition=EXCLUDED.definition,
                        target_symbol=EXCLUDED.target_symbol,
                        proposal_spec=EXCLUDED.proposal_spec
                    WHERE knowledge.ontology_discovery_candidates.status='proposed'
                    RETURNING id, domain_profile, kind, symbol, label, definition,
                              target_symbol, proposal_spec, fingerprint, status,
                              occurrence_count, created_at, updated_at, reviewed_at,
                              reviewed_by, review_note
                    """,
                    (
                        candidate.id,
                        context.workspace,
                        context.principal_id,
                        candidate.domain_profile,
                        candidate.kind,
                        candidate.symbol,
                        candidate.label,
                        candidate.definition,
                        candidate.target_symbol,
                        _json(spec_payload) if spec_payload is not None else None,
                        candidate.fingerprint,
                        candidate.status,
                        candidate.occurrence_count,
                        candidate.created_at,
                        candidate.updated_at,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        """
                        SELECT id, domain_profile, kind, symbol, label, definition,
                               target_symbol, proposal_spec, fingerprint, status,
                               occurrence_count, created_at, updated_at, reviewed_at,
                               reviewed_by, review_note
                        FROM knowledge.ontology_discovery_candidates
                        WHERE workspace_id=%s AND fingerprint=%s
                        """,
                        (context.workspace, candidate.fingerprint),
                    )
                    row = cursor.fetchone()
                if row is None:
                    raise NotFoundError("ontology discovery candidate not found")
                stored = _candidate(row)
                self._record_event(
                    cursor,
                    context,
                    InteractionEvent(
                        kind="ontology_discovery_proposed",
                        created_at=candidate.created_at,
                        candidate_id=stored.id,
                    ),
                )
            connection.commit()
        return stored

    def list_ontology_discovery_candidates(
        self,
        context: RequestContext,
        *,
        status: DiscoveryStatus | None,
        limit: int,
    ) -> list[OntologyDiscoveryCandidate]:
        with (
            self._database._connection(context) as connection,
            connection.cursor() as cursor,
        ):
            if status is None:
                cursor.execute(
                    """
                    SELECT id, domain_profile, kind, symbol, label, definition,
                           target_symbol, proposal_spec, fingerprint, status,
                           occurrence_count, created_at, updated_at, reviewed_at,
                           reviewed_by, review_note
                    FROM knowledge.ontology_discovery_candidates
                    WHERE workspace_id=%s
                    ORDER BY updated_at DESC, id DESC
                    LIMIT %s
                    """,
                    (context.workspace, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, domain_profile, kind, symbol, label, definition,
                           target_symbol, proposal_spec, fingerprint, status,
                           occurrence_count, created_at, updated_at, reviewed_at,
                           reviewed_by, review_note
                    FROM knowledge.ontology_discovery_candidates
                    WHERE workspace_id=%s AND status=%s
                    ORDER BY updated_at DESC, id DESC
                    LIMIT %s
                    """,
                    (context.workspace, status, limit),
                )
            return [_candidate(row) for row in cursor.fetchall()]

    def get_ontology_discovery_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
    ) -> OntologyDiscoveryCandidate:
        with (
            self._database._connection(context) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                SELECT id, domain_profile, kind, symbol, label, definition,
                       target_symbol, proposal_spec, fingerprint, status,
                       occurrence_count, created_at, updated_at, reviewed_at,
                       reviewed_by, review_note
                FROM knowledge.ontology_discovery_candidates
                WHERE workspace_id=%s AND id=%s
                """,
                (context.workspace, candidate_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise NotFoundError("ontology discovery candidate not found")
        return _candidate(row)

    def review_ontology_discovery_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        review: OntologyDiscoveryReview,
        *,
        now: datetime,
    ) -> OntologyDiscoveryCandidate:
        with self._database._connection(context) as connection:
            self._database._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, domain_profile, kind, symbol, label, definition,
                           target_symbol, proposal_spec, fingerprint, status,
                           occurrence_count, created_at, updated_at, reviewed_at,
                           reviewed_by, review_note
                    FROM knowledge.ontology_discovery_candidates
                    WHERE workspace_id=%s AND id=%s
                    FOR UPDATE
                    """,
                    (context.workspace, candidate_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise NotFoundError("ontology discovery candidate not found")
                candidate = _candidate(row)
                if candidate.status != "proposed":
                    raise ConflictError(
                        "ontology discovery candidate has already been reviewed"
                    )
                status = (
                    "accepted_for_release"
                    if review.action == "accept"
                    else "rejected"
                )
                cursor.execute(
                    """
                    UPDATE knowledge.ontology_discovery_candidates
                    SET status=%s, updated_at=%s, reviewed_at=%s, reviewed_by=%s,
                        review_note=%s
                    WHERE workspace_id=%s AND id=%s
                    RETURNING id, domain_profile, kind, symbol, label, definition,
                              target_symbol, proposal_spec, fingerprint, status,
                              occurrence_count, created_at, updated_at, reviewed_at,
                              reviewed_by, review_note
                    """,
                    (
                        status,
                        now,
                        now,
                        context.principal_id,
                        review.note,
                        context.workspace,
                        candidate_id,
                    ),
                )
                stored_row = cursor.fetchone()
                if stored_row is None:
                    raise NotFoundError("ontology discovery candidate not found")
                stored = _candidate(stored_row)
                self._record_event(
                    cursor,
                    context,
                    InteractionEvent(
                        kind="ontology_discovery_reviewed",
                        created_at=now,
                        candidate_id=stored.id,
                    ),
                )
            connection.commit()
        return stored

    def delete_expired_clarifications(
        self,
        context: RequestContext,
        *,
        before: datetime,
    ) -> int:
        with self._database._connection(context) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM interaction.clarifications
                    WHERE workspace_id=%s AND expires_at < %s
                    """,
                    (context.workspace, before),
                )
                deleted = cursor.rowcount
            connection.commit()
        return deleted

    @staticmethod
    def _upsert_preference(
        cursor: Any,
        context: RequestContext,
        preference: UserPreferenceWrite,
        *,
        now: datetime,
    ) -> UserPreference:
        stored = UserPreference(
            key=preference.key,
            values=preference.values,
            created_at=now,
            updated_at=now,
        )
        cursor.execute(
            """
            INSERT INTO interaction.preferences(
                id, workspace_id, principal_id, preference_key, values,
                created_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)
            ON CONFLICT (workspace_id, principal_id, preference_key) DO UPDATE
            SET values=EXCLUDED.values, updated_at=EXCLUDED.updated_at
            RETURNING id, preference_key, values, created_at, updated_at
            """,
            (
                stored.id,
                context.workspace,
                context.principal_id,
                stored.key,
                _json(stored.values),
                stored.created_at,
                stored.updated_at,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise ConflictError("preference write did not return a record")
        preference_record = _preference(row)
        PostgresInteractionStore._record_event(
            cursor,
            context,
            InteractionEvent(
                kind="preference_saved",
                created_at=now,
                preference_id=preference_record.id,
            ),
        )
        return preference_record

    @staticmethod
    def _record_event(
        cursor: Any,
        context: RequestContext,
        event: InteractionEvent,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO interaction.events(
                id, workspace_id, principal_id, kind, clarification_id,
                preference_id, feedback_id, candidate_id, outcome, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                event.id,
                context.workspace,
                context.principal_id,
                event.kind,
                event.clarification_id,
                event.preference_id,
                event.feedback_id,
                event.candidate_id,
                event.outcome,
                event.created_at,
            ),
        )


def _json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _question(row: dict[str, Any]) -> ClarificationQuestion:
    return ClarificationQuestion(
        id=row["id"],
        reason=row["reason"],
        prompt=row["prompt"],
        choices=_json_value(row["choices"]),
        allow_freeform=row["allow_freeform"],
        allow_multiple=row["allow_multiple"],
        preference_key=row["preference_key"],
        status=row["status"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


def _preference(row: dict[str, Any]) -> UserPreference:
    return UserPreference(
        id=row["id"],
        key=row["preference_key"],
        values=_json_value(row["values"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _candidate(row: dict[str, Any]) -> OntologyDiscoveryCandidate:
    raw_spec = row["proposal_spec"]
    spec: dict[str, Any] = _json_value(raw_spec) if raw_spec is not None else {}
    return OntologyDiscoveryCandidate(
        id=row["id"],
        domain_profile=row["domain_profile"],
        kind=row["kind"],
        symbol=row["symbol"],
        label=row["label"],
        definition=row["definition"],
        target_symbol=row["target_symbol"],
        parent=spec.get("parent"),
        domain=spec.get("domain"),
        range=spec.get("range"),
        inverse=spec.get("inverse"),
        risk=spec.get("risk"),
        review=spec.get("review"),
        extraction=spec.get("extraction"),
        status=row["status"],
        occurrence_count=row["occurrence_count"],
        fingerprint=row["fingerprint"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        reviewed_at=row["reviewed_at"],
        reviewed_by=row["reviewed_by"],
        review_note=row["review_note"],
    )
