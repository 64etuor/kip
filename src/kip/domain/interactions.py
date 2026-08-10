from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kip.domain.telemetry import safe_request_id
from kip.errors import ValidationError
from kip.ids import new_id

_OPTION_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SYMBOL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

ClarificationReason = Literal[
    "ambiguous_term",
    "scope_selection",
    "preference",
    "other",
]
ClarificationStatus = Literal["open", "answered", "expired"]
FeedbackOutcome = Literal["helpful", "not_helpful", "needs_clarification"]
DiscoveryKind = Literal["entity_type", "predicate", "controlled_value", "alias"]
DiscoveryStatus = Literal["proposed", "accepted_for_release", "rejected"]


class InteractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _display(value: str, *, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    return normalized


def _normalized_preference_key(value: str) -> str:
    if _SYMBOL_RE.fullmatch(value) is None:
        raise ValueError("preference key is invalid")
    return value


def validate_preference_key(value: str) -> str:
    try:
        return _normalized_preference_key(value)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


class ClarificationChoice(InteractionModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=140)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if _OPTION_ID_RE.fullmatch(value) is None:
            raise ValueError("clarification choice ID is invalid")
        return value

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return _display(value, field_name="clarification choice label")


class ClarificationRequest(InteractionModel):
    reason: ClarificationReason
    prompt: str = Field(min_length=1, max_length=500)
    choices: list[ClarificationChoice] = Field(default_factory=list, max_length=4)
    allow_freeform: bool = True
    allow_multiple: bool = False
    preference_key: str | None = Field(default=None, max_length=64)

    @field_validator("prompt")
    @classmethod
    def normalize_prompt(cls, value: str) -> str:
        return _display(value, field_name="clarification prompt")

    @field_validator("preference_key")
    @classmethod
    def valid_preference_key(cls, value: str | None) -> str | None:
        return _normalized_preference_key(value) if value is not None else None

    @model_validator(mode="after")
    def valid_choices(self) -> Self:
        ids = [choice.id for choice in self.choices]
        if len(ids) != len(set(ids)):
            raise ValueError("clarification choices must have unique IDs")
        if not self.choices and not self.allow_freeform:
            raise ValueError("clarification requires a choice or freeform response")
        return self


class ClarificationQuestion(ClarificationRequest):
    schema_version: Literal["kip.clarification.v1"] = "kip.clarification.v1"
    id: str = Field(default_factory=lambda: new_id("clrq"), min_length=1, max_length=128)
    status: ClarificationStatus = "open"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime

    @model_validator(mode="after")
    def valid_expiry(self) -> Self:
        if self.created_at.utcoffset() is None or self.expires_at.utcoffset() is None:
            raise ValueError("clarification timestamps must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("clarification expiry must be later than creation")
        return self


class ClarificationAnswer(InteractionModel):
    question_id: str = Field(min_length=1, max_length=128)
    option_ids: list[str] = Field(default_factory=list, max_length=4)
    freeform: str | None = Field(default=None, max_length=500)
    remember: bool = False

    @field_validator("option_ids")
    @classmethod
    def valid_option_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("clarification answer option IDs must be unique")
        if any(_OPTION_ID_RE.fullmatch(value) is None for value in values):
            raise ValueError("clarification answer option ID is invalid")
        return values

    @field_validator("freeform")
    @classmethod
    def normalize_freeform(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _display(value, field_name="clarification freeform response")

    @model_validator(mode="after")
    def has_response(self) -> Self:
        if not self.option_ids and self.freeform is None:
            raise ValueError("clarification answer requires a value")
        return self


class UserPreferenceWrite(InteractionModel):
    key: str = Field(min_length=1, max_length=64)
    values: list[str] = Field(min_length=1, max_length=5)
    confirmed: Literal[True]

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        return _normalized_preference_key(value)

    @field_validator("values")
    @classmethod
    def normalized_values(cls, values: list[str]) -> list[str]:
        normalized = [_display(value, field_name="preference value") for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("preference values must be unique")
        return normalized


class UserPreference(InteractionModel):
    schema_version: Literal["kip.user-preference.v1"] = "kip.user-preference.v1"
    id: str = Field(default_factory=lambda: new_id("pref"), min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=64)
    values: list[str] = Field(min_length=1, max_length=5)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        return _normalized_preference_key(value)

    @field_validator("values")
    @classmethod
    def normalized_values(cls, values: list[str]) -> list[str]:
        return UserPreferenceWrite.normalized_values(values)


class ClarificationResolution(InteractionModel):
    schema_version: Literal["kip.clarification-resolution.v1"] = (
        "kip.clarification-resolution.v1"
    )
    question: ClarificationQuestion
    selected_values: list[str] = Field(min_length=1, max_length=5)
    preference: UserPreference | None = None


class FeedbackSubmission(InteractionModel):
    request_id: str | None = Field(default=None, max_length=128)
    outcome: FeedbackOutcome
    reason_codes: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("request_id")
    @classmethod
    def valid_request_id(cls, value: str | None) -> str | None:
        if value is not None and safe_request_id(value) is None:
            raise ValueError("feedback request ID is invalid")
        return value

    @field_validator("reason_codes")
    @classmethod
    def valid_reason_codes(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("feedback reason codes must be unique")
        if any(_SYMBOL_RE.fullmatch(value) is None for value in values):
            raise ValueError("feedback reason code is invalid")
        return values


class InteractionFeedback(InteractionModel):
    schema_version: Literal["kip.interaction-feedback.v1"] = "kip.interaction-feedback.v1"
    id: str = Field(default_factory=lambda: new_id("ifb"), min_length=1, max_length=128)
    request_id: str | None = None
    outcome: FeedbackOutcome
    reason_codes: list[str] = Field(default_factory=list, max_length=4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OntologyDiscoveryProposal(InteractionModel):
    kind: DiscoveryKind
    symbol: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=140)
    definition: str = Field(min_length=1, max_length=500)
    target_symbol: str | None = Field(default=None, max_length=64)
    confirmed: Literal[True]

    @field_validator("symbol", "target_symbol")
    @classmethod
    def valid_symbol(cls, value: str | None) -> str | None:
        if value is not None and _SYMBOL_RE.fullmatch(value) is None:
            raise ValueError("ontology discovery symbol is invalid")
        return value

    @field_validator("label", "definition")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _display(value, field_name="ontology discovery text")

    @model_validator(mode="after")
    def target_matches_kind(self) -> Self:
        target_required = self.kind in {"controlled_value", "alias"}
        if target_required and self.target_symbol is None:
            raise ValueError("ontology discovery target is required for this kind")
        if not target_required and self.target_symbol is not None:
            raise ValueError("ontology discovery target is only allowed for value or alias")
        return self


class OntologyDiscoveryCandidate(InteractionModel):
    schema_version: Literal["kip.ontology-discovery-candidate.v1"] = (
        "kip.ontology-discovery-candidate.v1"
    )
    id: str = Field(default_factory=lambda: new_id("odc"), min_length=1, max_length=128)
    domain_profile: str = Field(min_length=1, max_length=64)
    kind: DiscoveryKind
    symbol: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=140)
    definition: str = Field(min_length=1, max_length=500)
    target_symbol: str | None = Field(default=None, max_length=64)
    status: DiscoveryStatus = "proposed"
    occurrence_count: int = Field(default=1, ge=1)
    fingerprint: str = Field(min_length=1, max_length=128, exclude=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reviewed_at: datetime | None = None
    reviewed_by: str | None = Field(default=None, max_length=128)
    review_note: str | None = Field(default=None, max_length=500)


class OntologyDiscoveryReview(InteractionModel):
    action: Literal["accept", "reject"]
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _display(value, field_name="ontology discovery review note")


class InteractionEvent(InteractionModel):
    schema_version: Literal["kip.interaction-event.v1"] = "kip.interaction-event.v1"
    id: str = Field(default_factory=lambda: new_id("iev"), min_length=1, max_length=128)
    kind: Literal[
        "clarification_answered",
        "preference_saved",
        "preference_deleted",
        "feedback_submitted",
        "ontology_discovery_proposed",
        "ontology_discovery_reviewed",
    ]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    clarification_id: str | None = None
    preference_id: str | None = None
    feedback_id: str | None = None
    candidate_id: str | None = None
    outcome: FeedbackOutcome | None = None


def resolved_clarification_values(
    question: ClarificationQuestion,
    answer: ClarificationAnswer,
) -> list[str]:
    if question.id != answer.question_id:
        raise ValidationError("clarification answer does not match the question")
    known_option_ids = {choice.id for choice in question.choices}
    unknown = sorted(set(answer.option_ids).difference(known_option_ids))
    if unknown:
        raise ValidationError(
            "clarification answer references unknown choices: " + ", ".join(unknown)
        )
    if not question.allow_multiple and len(answer.option_ids) > 1:
        raise ValidationError("clarification does not allow multiple choices")
    if answer.freeform is not None and not question.allow_freeform:
        raise ValidationError("clarification does not allow a freeform response")
    return [*answer.option_ids, *([answer.freeform] if answer.freeform else [])]
