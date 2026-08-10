from __future__ import annotations

from datetime import datetime
from typing import Protocol

from kip.domain.interactions import (
    ClarificationAnswer,
    ClarificationQuestion,
    ClarificationResolution,
    DiscoveryStatus,
    FeedbackSubmission,
    InteractionFeedback,
    OntologyDiscoveryCandidate,
    OntologyDiscoveryReview,
    UserPreference,
    UserPreferenceWrite,
)
from kip.domain.models import RequestContext


class InteractionStore(Protocol):
    def create_clarification(
        self,
        context: RequestContext,
        question: ClarificationQuestion,
    ) -> ClarificationQuestion: ...

    def get_clarification(
        self,
        context: RequestContext,
        question_id: str,
        *,
        now: datetime,
    ) -> ClarificationQuestion: ...

    def answer_clarification(
        self,
        context: RequestContext,
        answer: ClarificationAnswer,
        *,
        now: datetime,
    ) -> ClarificationResolution: ...

    def list_preferences(
        self,
        context: RequestContext,
    ) -> list[UserPreference]: ...

    def upsert_preference(
        self,
        context: RequestContext,
        preference: UserPreferenceWrite,
        *,
        now: datetime,
    ) -> UserPreference: ...

    def delete_preference(
        self,
        context: RequestContext,
        key: str,
        *,
        now: datetime,
    ) -> bool: ...

    def record_feedback(
        self,
        context: RequestContext,
        submission: FeedbackSubmission,
        *,
        now: datetime,
    ) -> InteractionFeedback: ...

    def save_ontology_discovery_candidate(
        self,
        context: RequestContext,
        candidate: OntologyDiscoveryCandidate,
    ) -> OntologyDiscoveryCandidate: ...

    def list_ontology_discovery_candidates(
        self,
        context: RequestContext,
        *,
        status: DiscoveryStatus | None,
        limit: int,
    ) -> list[OntologyDiscoveryCandidate]: ...

    def review_ontology_discovery_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        review: OntologyDiscoveryReview,
        *,
        now: datetime,
    ) -> OntologyDiscoveryCandidate: ...

    def delete_expired_clarifications(
        self,
        context: RequestContext,
        *,
        before: datetime,
    ) -> int: ...
