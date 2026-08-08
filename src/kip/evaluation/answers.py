from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnswerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewedClaim(AnswerModel):
    id: str = Field(min_length=1)
    expected_claim: str | None = None
    supported_by_evidence: bool
    citation_locator_correct: bool | None = None


class AnswerReview(AnswerModel):
    schema_version: str = "kip.answer-review.v1"
    case_id: str = Field(min_length=1)
    expected_claims: tuple[str, ...] = ()
    claims: tuple[ReviewedClaim, ...] = ()
    expected_refusal: bool | None = None
    refused: bool | None = None

    @model_validator(mode="after")
    def annotations_are_consistent(self) -> Self:
        claim_ids = [claim.id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("reviewed claim ids must be unique")
        if len(self.expected_claims) != len(set(self.expected_claims)):
            raise ValueError("expected claim ids must be unique")
        unknown = {
            claim.expected_claim
            for claim in self.claims
            if claim.expected_claim is not None
            and claim.expected_claim not in self.expected_claims
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
    matched = {
        claim.expected_claim for claim in review.claims if claim.expected_claim is not None
    }
    completeness = (
        len(matched) / len(review.expected_claims) if review.expected_claims else None
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
        unsupported_claim_count=sum(
            not claim.supported_by_evidence for claim in review.claims
        ),
    )
