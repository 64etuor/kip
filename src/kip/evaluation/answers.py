from __future__ import annotations

from collections.abc import Sequence
from statistics import fmean
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AnswerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewedClaim(AnswerModel):
    id: str = Field(min_length=1)
    expected_claim: str | None = None
    supported_by_evidence: bool
    citation_locator_correct: bool | None = None
    cited_evidence_ids: tuple[str, ...] = ()

    @field_validator("cited_evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("claim citation evidence IDs must be unique")
        return values


class AnswerReview(AnswerModel):
    schema_version: str = "kip.answer-review.v1"
    case_id: str = Field(min_length=1)
    expected_claims: tuple[str, ...] | None = None
    expected_evidence_ids: tuple[str, ...] | None = None
    claims: tuple[ReviewedClaim, ...] = ()
    expected_refusal: bool | None = None
    refused: bool | None = None

    @model_validator(mode="after")
    def annotations_are_consistent(self) -> Self:
        claim_ids = [claim.id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("reviewed claim ids must be unique")
        if self.expected_claims is not None and len(self.expected_claims) != len(
            set(self.expected_claims)
        ):
            raise ValueError("expected claim ids must be unique")
        if self.expected_evidence_ids is not None and len(self.expected_evidence_ids) != len(
            set(self.expected_evidence_ids)
        ):
            raise ValueError("expected evidence ids must be unique")
        unknown = {
            claim.expected_claim
            for claim in self.claims
            if claim.expected_claim is not None
            and (self.expected_claims is None or claim.expected_claim not in self.expected_claims)
        }
        if unknown:
            raise ValueError(f"reviewed claims reference unknown expectations: {sorted(unknown)}")
        if self.refused is True and self.claims:
            raise ValueError("a refused answer cannot contain reviewed claims")
        return self


class AnswerMetrics(AnswerModel):
    schema_version: str = "kip.answer-metrics.v1"
    case_id: str
    groundedness: float | None = Field(default=None, ge=0.0, le=1.0)
    completeness: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    refusal_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    claim_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    claim_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    refusal_appropriateness: float | None = Field(default=None, ge=0.0, le=1.0)
    unsupported_claim_count: int = Field(ge=0)


class AnswerAggregateMetrics(AnswerModel):
    schema_version: str = "kip.answer-aggregate-metrics.v1"
    case_count: int = Field(ge=0)
    groundedness: float | None = Field(default=None, ge=0.0, le=1.0)
    completeness: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    refusal_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    claim_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    claim_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    refusal_appropriateness: float | None = Field(default=None, ge=0.0, le=1.0)
    unsupported_claim_count: int = Field(ge=0)


def evaluate_answer(review: AnswerReview) -> AnswerMetrics:
    groundedness = (
        sum(claim.supported_by_evidence for claim in review.claims) / len(review.claims)
        if review.claims
        else None
    )
    cited = [
        claim.citation_locator_correct
        for claim in review.claims
        if claim.citation_locator_correct is not None
    ]
    citation_accuracy = sum(cited) / len(cited) if cited else None
    correctly_matched = {
        claim.expected_claim
        for claim in review.claims
        if claim.expected_claim is not None and claim.supported_by_evidence
    }
    completeness = None
    if review.expected_claims is not None:
        completeness = (
            len(correctly_matched) / len(review.expected_claims)
            if review.expected_claims
            else float(not correctly_matched)
        )
    claim_precision = (
        len(correctly_matched) / len(review.claims)
        if review.claims
        else (float(not review.expected_claims) if review.expected_claims is not None else None)
    )
    actual_evidence = {
        evidence_id for claim in review.claims for evidence_id in claim.cited_evidence_ids
    }
    expected_evidence = (
        set(review.expected_evidence_ids) if review.expected_evidence_ids is not None else None
    )
    citation_precision = None
    citation_recall = None
    if expected_evidence is not None:
        citation_precision = (
            len(actual_evidence.intersection(expected_evidence)) / len(actual_evidence)
            if actual_evidence
            else float(not expected_evidence)
        )
        citation_recall = (
            len(actual_evidence.intersection(expected_evidence)) / len(expected_evidence)
            if expected_evidence
            else 1.0
        )
    refusal_accuracy = (
        float(review.expected_refusal is review.refused)
        if review.expected_refusal is not None and review.refused is not None
        else None
    )
    return AnswerMetrics(
        case_id=review.case_id,
        groundedness=groundedness,
        completeness=completeness,
        citation_accuracy=citation_accuracy,
        refusal_accuracy=refusal_accuracy,
        claim_precision=claim_precision,
        claim_recall=completeness,
        citation_precision=citation_precision,
        citation_recall=citation_recall,
        refusal_appropriateness=refusal_accuracy,
        unsupported_claim_count=sum(not claim.supported_by_evidence for claim in review.claims),
    )


def aggregate_answer_metrics(
    metrics: Sequence[AnswerMetrics],
) -> AnswerAggregateMetrics:
    return AnswerAggregateMetrics(
        case_count=len(metrics),
        groundedness=_average(metrics, "groundedness"),
        completeness=_average(metrics, "completeness"),
        citation_accuracy=_average(metrics, "citation_accuracy"),
        refusal_accuracy=_average(metrics, "refusal_accuracy"),
        claim_precision=_average(metrics, "claim_precision"),
        claim_recall=_average(metrics, "claim_recall"),
        citation_precision=_average(metrics, "citation_precision"),
        citation_recall=_average(metrics, "citation_recall"),
        refusal_appropriateness=_average(metrics, "refusal_appropriateness"),
        unsupported_claim_count=sum(item.unsupported_claim_count for item in metrics),
    )


def _average(metrics: Sequence[AnswerMetrics], field: str) -> float | None:
    values = [getattr(item, field) for item in metrics if getattr(item, field) is not None]
    return fmean(values) if values else None
