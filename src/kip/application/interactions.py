from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from kip.domain.interactions import (
    ClarificationAnswer,
    ClarificationQuestion,
    ClarificationRequest,
    ClarificationResolution,
    DiscoveryStatus,
    FeedbackSubmission,
    InteractionFeedback,
    OntologyDiscoveryCandidate,
    OntologyDiscoveryProposal,
    OntologyDiscoveryReview,
    UserPreference,
    UserPreferenceWrite,
    validate_preference_key,
)
from kip.domain.models import RequestContext
from kip.errors import AuthorizationError, ValidationError
from kip.ids import stable_id
from kip.ports.interactions import InteractionStore



class InteractionUseCases:
    def __init__(
        self,
        store: InteractionStore,
        *,
        enabled: bool,
        discovery_enabled: bool,
        domain_profile: str,
        clarification_ttl_seconds: int,
    ) -> None:
        self._store = store
        self._enabled = enabled
        self._discovery_enabled = discovery_enabled
        self._domain_profile = domain_profile
        self._clarification_ttl = timedelta(seconds=clarification_ttl_seconds)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def create_clarification(
        self,
        context: RequestContext,
        request: ClarificationRequest,
        *,
        now: datetime | None = None,
    ) -> ClarificationQuestion:
        self._require_enabled()
        selected_now = now or datetime.now(UTC)
        return self._store.create_clarification(
            context,
            ClarificationQuestion(
                **request.model_dump(),
                created_at=selected_now,
                expires_at=selected_now + self._clarification_ttl,
            ),
        )

    def get_clarification(
        self,
        context: RequestContext,
        question_id: str,
        *,
        now: datetime | None = None,
    ) -> ClarificationQuestion:
        self._require_enabled()
        return self._store.get_clarification(
            context,
            question_id,
            now=now or datetime.now(UTC),
        )

    def answer_clarification(
        self,
        context: RequestContext,
        answer: ClarificationAnswer,
        *,
        now: datetime | None = None,
    ) -> ClarificationResolution:
        self._require_enabled()
        return self._store.answer_clarification(
            context,
            answer,
            now=now or datetime.now(UTC),
        )

    def list_preferences(
        self,
        context: RequestContext,
    ) -> list[UserPreference]:
        self._require_enabled()
        return self._store.list_preferences(context)

    def save_preference(
        self,
        context: RequestContext,
        preference: UserPreferenceWrite,
        *,
        now: datetime | None = None,
    ) -> UserPreference:
        self._require_enabled()
        return self._store.upsert_preference(
            context,
            preference,
            now=now or datetime.now(UTC),
        )

    def delete_preference(
        self,
        context: RequestContext,
        key: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        self._require_enabled()
        return self._store.delete_preference(
            context,
            validate_preference_key(key),
            now=now or datetime.now(UTC),
        )

    def submit_feedback(
        self,
        context: RequestContext,
        submission: FeedbackSubmission,
        *,
        now: datetime | None = None,
    ) -> InteractionFeedback:
        self._require_enabled()
        return self._store.record_feedback(
            context,
            submission,
            now=now or datetime.now(UTC),
        )

    def propose_ontology_discovery(
        self,
        context: RequestContext,
        proposal: OntologyDiscoveryProposal,
        *,
        now: datetime | None = None,
    ) -> OntologyDiscoveryCandidate:
        self._require_enabled()
        if not self._discovery_enabled:
            raise ValidationError("ontology discovery is disabled")
        selected_now = now or datetime.now(UTC)
        fingerprint = _discovery_fingerprint(
            self._domain_profile,
            context.principal_id,
            proposal,
        )
        return self._store.save_ontology_discovery_candidate(
            context,
            OntologyDiscoveryCandidate(
                id=stable_id("odc", context.workspace, fingerprint),
                domain_profile=self._domain_profile,
                kind=proposal.kind,
                symbol=proposal.symbol,
                label=proposal.label,
                definition=proposal.definition,
                target_symbol=proposal.target_symbol,
                fingerprint=fingerprint,
                created_at=selected_now,
                updated_at=selected_now,
            ),
        )

    def list_ontology_discovery_candidates(
        self,
        context: RequestContext,
        *,
        status: str | None = "proposed",
        limit: int = 100,
    ) -> list[OntologyDiscoveryCandidate]:
        # Authorization is checked before the feature flag so a missing
        # role is never masked by the feature being disabled.
        self._require_admin(context)
        self._require_enabled()
        if not 1 <= limit <= 1000:
            raise ValidationError("ontology discovery limit must be between 1 and 1000")
        return self._store.list_ontology_discovery_candidates(
            context,
            status=_discovery_status(status),
            limit=limit,
        )

    def review_ontology_discovery_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        review: OntologyDiscoveryReview,
        *,
        now: datetime | None = None,
    ) -> OntologyDiscoveryCandidate:
        # Authorization is checked before the feature flag so a missing
        # role is never masked by the feature being disabled.
        self._require_admin(context)
        self._require_enabled()
        return self._store.review_ontology_discovery_candidate(
            context,
            candidate_id,
            review,
            now=now or datetime.now(UTC),
        )

    def prune_expired_clarifications(
        self,
        context: RequestContext,
        *,
        now: datetime | None = None,
    ) -> int:
        # Authorization is checked before the feature flag so a missing
        # role is never masked by the feature being disabled.
        self._require_admin(context)
        self._require_enabled()
        return self._store.delete_expired_clarifications(
            context,
            before=now or datetime.now(UTC),
        )

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise ValidationError("interaction memory is disabled")

    @staticmethod
    def _require_admin(context: RequestContext) -> None:
        if "admin" not in context.roles:
            raise AuthorizationError("admin role is required for ontology discovery")


def _discovery_fingerprint(
    domain_profile: str,
    principal_id: str,
    proposal: OntologyDiscoveryProposal,
) -> str:
    payload = {
        "domain_profile": domain_profile,
        "principal_id": principal_id,
        "kind": proposal.kind,
        "symbol": proposal.symbol,
        "target_symbol": proposal.target_symbol,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _discovery_status(value: str | None) -> DiscoveryStatus | None:
    if value is None:
        return None
    match value:
        case "proposed":
            return "proposed"
        case "accepted_for_release":
            return "accepted_for_release"
        case "rejected":
            return "rejected"
        case _:
            raise ValidationError("unknown ontology discovery status")
