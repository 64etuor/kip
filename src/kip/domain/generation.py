from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kip.errors import ValidationError


class GenerationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GenerationEvidence(GenerationModel):
    id: str = Field(min_length=1)
    body: str = Field(min_length=1)
    locator: str = Field(min_length=1)


class GenerationRequest(GenerationModel):
    query: str = Field(min_length=1)
    evidence: tuple[GenerationEvidence, ...] = Field(min_length=1)
    max_claims: int = Field(default=8, ge=1, le=64)
    max_output_tokens: int = Field(default=1024, ge=64, le=32768)

    @field_validator("evidence")
    @classmethod
    def unique_evidence(
        cls,
        evidence: tuple[GenerationEvidence, ...],
    ) -> tuple[GenerationEvidence, ...]:
        ids = tuple(item.id for item in evidence)
        if len(ids) != len(set(ids)):
            raise ValueError("evidence IDs must be unique")
        return evidence


class GeneratedClaim(GenerationModel):
    text: str = Field(min_length=1)
    evidence_ids: tuple[str, ...]
    certainty: Literal["supported", "uncertain"]

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, evidence_ids: tuple[str, ...]) -> tuple[str, ...]:
        if any(not evidence_id for evidence_id in evidence_ids):
            raise ValueError("evidence IDs must not be empty")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        return evidence_ids

    @model_validator(mode="after")
    def supported_claim_has_evidence(self) -> GeneratedClaim:
        if self.certainty == "supported" and not self.evidence_ids:
            raise ValueError("supported claim requires evidence")
        return self


class ModelRevision(GenerationModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    revision: str = Field(min_length=1)


class GenerationUsage(GenerationModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def total_is_consistent(self) -> GenerationUsage:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens plus output_tokens")
        return self


class GenerationResult(GenerationModel):
    claims: tuple[GeneratedClaim, ...] = Field(min_length=1)
    model: ModelRevision
    usage: GenerationUsage
    provider_request_id: str | None = None


def validate_generation_result(
    result: GenerationResult,
    *,
    allowed_evidence_ids: tuple[str, ...],
    max_claims: int,
) -> GenerationResult:
    if len(result.claims) > max_claims:
        raise ValidationError(
            f"generation claim count {len(result.claims)} exceeds limit {max_claims}"
        )
    allowed = set(allowed_evidence_ids)
    referenced = {
        evidence_id
        for claim in result.claims
        for evidence_id in claim.evidence_ids
    }
    unknown = sorted(referenced - allowed)
    if unknown:
        raise ValidationError(
            "generation returned unknown evidence IDs: " + ", ".join(unknown)
        )
    return result
