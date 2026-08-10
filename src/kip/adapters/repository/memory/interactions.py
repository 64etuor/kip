from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from kip.adapters.repository.memory.state import MemoryState
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
from kip.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError


@dataclass(frozen=True, slots=True)
class MemoryInteractionStore:
    state: MemoryState

    def create_clarification(
        self,
        context: RequestContext,
        question: ClarificationQuestion,
    ) -> ClarificationQuestion:
        if question.status != "open":
            raise ValidationError("new clarification must be open")
        if question.id in self.state.clarifications:
            raise ConflictError(f"clarification already exists: {question.id}")
        self.state.clarifications[question.id] = (
            context.workspace,
            context.principal_id,
            question.model_copy(deep=True),
        )
        return question.model_copy(deep=True)

    def get_clarification(
        self,
        context: RequestContext,
        question_id: str,
        *,
        now: datetime,
    ) -> ClarificationQuestion:
        question = self._owned_question(context, question_id)
        if question.status == "open" and question.expires_at <= now:
            question = question.model_copy(update={"status": "expired"})
            self._replace_question(context, question)
        return question.model_copy(deep=True)

    def answer_clarification(
        self,
        context: RequestContext,
        answer: ClarificationAnswer,
        *,
        now: datetime,
    ) -> ClarificationResolution:
        question = self.get_clarification(
            context,
            answer.question_id,
            now=now,
        )
        if question.status == "expired":
            raise ConflictError("clarification has expired")
        if question.status != "open":
            raise ConflictError("clarification has already been answered")
        selected_values = resolved_clarification_values(question, answer)
        preference: UserPreference | None = None
        if answer.remember:
            if question.preference_key is None:
                raise ValidationError("clarification does not permit remembered preferences")
            preference = self._upsert_preference(
                context,
                UserPreferenceWrite(
                    key=question.preference_key,
                    values=selected_values,
                    confirmed=True,
                ),
                now=now,
            )
        answered = question.model_copy(update={"status": "answered"})
        self._replace_question(context, answered)
        self._record_event(
            context,
            InteractionEvent(
                kind="clarification_answered",
                created_at=now,
                clarification_id=question.id,
                preference_id=preference.id if preference is not None else None,
            ),
        )
        return ClarificationResolution(
            question=answered,
            selected_values=selected_values,
            preference=preference,
        )

    def list_preferences(
        self,
        context: RequestContext,
    ) -> list[UserPreference]:
        return [
            preference.model_copy(deep=True)
            for (workspace, principal, _), preference in sorted(
                self.state.preferences.items(),
                key=lambda item: (item[0][2], item[1].id),
            )
            if workspace == context.workspace and principal == context.principal_id
        ]

    def upsert_preference(
        self,
        context: RequestContext,
        preference: UserPreferenceWrite,
        *,
        now: datetime,
    ) -> UserPreference:
        return self._upsert_preference(context, preference, now=now)

    def delete_preference(
        self,
        context: RequestContext,
        key: str,
        *,
        now: datetime,
    ) -> bool:
        item_key = (context.workspace, context.principal_id, key)
        preference = self.state.preferences.pop(item_key, None)
        if preference is None:
            return False
        self._record_event(
            context,
            InteractionEvent(
                kind="preference_deleted",
                created_at=now,
                preference_id=preference.id,
            ),
        )
        return True

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
        self.state.interaction_feedback.append(
            (context.workspace, context.principal_id, feedback.model_copy(deep=True))
        )
        self._record_event(
            context,
            InteractionEvent(
                kind="feedback_submitted",
                created_at=now,
                feedback_id=feedback.id,
                outcome=feedback.outcome,
            ),
        )
        return feedback.model_copy(deep=True)

    def save_ontology_discovery_candidate(
        self,
        context: RequestContext,
        candidate: OntologyDiscoveryCandidate,
    ) -> OntologyDiscoveryCandidate:
        key = (context.workspace, candidate.fingerprint)
        existing_id = self.state.ontology_discovery_ids_by_fingerprint.get(key)
        if existing_id is not None:
            workspace, existing = self.state.ontology_discovery_candidates[existing_id]
            if workspace != context.workspace:
                raise NotFoundError("ontology discovery candidate not found")
            updated = existing.model_copy(
                update={
                    "occurrence_count": existing.occurrence_count + 1,
                    "updated_at": candidate.updated_at,
                }
            )
            self.state.ontology_discovery_candidates[existing_id] = (
                workspace,
                updated,
            )
            return updated.model_copy(deep=True)
        self.state.ontology_discovery_candidates[candidate.id] = (
            context.workspace,
            candidate.model_copy(deep=True),
        )
        self.state.ontology_discovery_ids_by_fingerprint[key] = candidate.id
        self._record_event(
            context,
            InteractionEvent(
                kind="ontology_discovery_proposed",
                created_at=candidate.created_at,
                candidate_id=candidate.id,
            ),
        )
        return candidate.model_copy(deep=True)

    def list_ontology_discovery_candidates(
        self,
        context: RequestContext,
        *,
        status: DiscoveryStatus | None,
        limit: int,
    ) -> list[OntologyDiscoveryCandidate]:
        self._require_admin(context)
        selected = [
            candidate.model_copy(deep=True)
            for workspace, candidate in self.state.ontology_discovery_candidates.values()
            if workspace == context.workspace
            and (status is None or candidate.status == status)
        ]
        selected.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return selected[:limit]

    def review_ontology_discovery_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        review: OntologyDiscoveryReview,
        *,
        now: datetime,
    ) -> OntologyDiscoveryCandidate:
        self._require_admin(context)
        stored = self.state.ontology_discovery_candidates.get(candidate_id)
        if stored is None or stored[0] != context.workspace:
            raise NotFoundError("ontology discovery candidate not found")
        candidate = stored[1]
        if candidate.status != "proposed":
            raise ConflictError("ontology discovery candidate has already been reviewed")
        status = "accepted_for_release" if review.action == "accept" else "rejected"
        reviewed = candidate.model_copy(
            update={
                "status": status,
                "updated_at": now,
                "reviewed_at": now,
                "reviewed_by": context.principal_id,
                "review_note": review.note,
            }
        )
        self.state.ontology_discovery_candidates[candidate_id] = (
            context.workspace,
            reviewed,
        )
        self._record_event(
            context,
            InteractionEvent(
                kind="ontology_discovery_reviewed",
                created_at=now,
                candidate_id=candidate_id,
            ),
        )
        return reviewed.model_copy(deep=True)

    def delete_expired_clarifications(
        self,
        context: RequestContext,
        *,
        before: datetime,
    ) -> int:
        self._require_admin(context)
        expired_ids = [
            question_id
            for question_id, (workspace, _, question) in self.state.clarifications.items()
            if workspace == context.workspace and question.expires_at < before
        ]
        for question_id in expired_ids:
            del self.state.clarifications[question_id]
        return len(expired_ids)

    def _owned_question(
        self,
        context: RequestContext,
        question_id: str,
    ) -> ClarificationQuestion:
        stored = self.state.clarifications.get(question_id)
        if (
            stored is None
            or stored[0] != context.workspace
            or stored[1] != context.principal_id
        ):
            raise NotFoundError("clarification not found")
        return stored[2].model_copy(deep=True)

    def _replace_question(
        self,
        context: RequestContext,
        question: ClarificationQuestion,
    ) -> None:
        self.state.clarifications[question.id] = (
            context.workspace,
            context.principal_id,
            question.model_copy(deep=True),
        )

    def _upsert_preference(
        self,
        context: RequestContext,
        preference: UserPreferenceWrite,
        *,
        now: datetime,
    ) -> UserPreference:
        key = (context.workspace, context.principal_id, preference.key)
        existing = self.state.preferences.get(key)
        stored = (
            UserPreference(
                key=preference.key,
                values=preference.values,
                created_at=now,
                updated_at=now,
            )
            if existing is None
            else existing.model_copy(
                update={"values": preference.values, "updated_at": now}
            )
        )
        self.state.preferences[key] = stored.model_copy(deep=True)
        self._record_event(
            context,
            InteractionEvent(
                kind="preference_saved",
                created_at=now,
                preference_id=stored.id,
            ),
        )
        return stored.model_copy(deep=True)

    def _record_event(
        self,
        context: RequestContext,
        event: InteractionEvent,
    ) -> None:
        self.state.interaction_events.append(
            (context.workspace, context.principal_id, event.model_copy(deep=True))
        )

    @staticmethod
    def _require_admin(context: RequestContext) -> None:
        if "admin" not in context.roles:
            raise AuthorizationError("admin role is required for ontology discovery")
