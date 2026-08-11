from __future__ import annotations

import pytest
from pydantic import ValidationError

from kip.domain.generation import (
    GeneratedClaim,
    GenerationEvidence,
    GenerationRequest,
    GenerationResult,
    GenerationUsage,
    ModelRevision,
    validate_generation_result,
)
from kip.errors import ValidationError as KipValidationError


def _result(*claims: GeneratedClaim) -> GenerationResult:
    return GenerationResult(
        claims=claims,
        model=ModelRevision(provider="openai", model="answer-model", revision="2026-08-01"),
        usage=GenerationUsage(input_tokens=10, output_tokens=4, total_tokens=14),
        provider_request_id="req_test",
    )


def test_generation_request_requires_unique_exact_evidence() -> None:
    evidence = GenerationEvidence(id="unit_1", body="승인 근거", locator="page:1")

    with pytest.raises(ValidationError, match="evidence IDs must be unique"):
        GenerationRequest(query="승인됐어?", evidence=(evidence, evidence))


def test_generation_result_rejects_unknown_evidence_ids() -> None:
    result = _result(
        GeneratedClaim(
            text="승인되었다.",
            evidence_ids=("unit_unknown",),
            certainty="supported",
        )
    )

    with pytest.raises(KipValidationError, match="unknown evidence IDs"):
        validate_generation_result(result, allowed_evidence_ids=("unit_1",), max_claims=4)


def test_supported_claim_requires_evidence_and_unique_references() -> None:
    with pytest.raises(ValidationError, match="supported claim requires evidence"):
        GeneratedClaim(text="승인되었다.", evidence_ids=(), certainty="supported")

    with pytest.raises(ValidationError, match="evidence IDs must be unique"):
        GeneratedClaim(
            text="승인되었다.",
            evidence_ids=("unit_1", "unit_1"),
            certainty="supported",
        )


def test_disputed_claim_must_cite_every_disagreeing_source() -> None:
    with pytest.raises(ValidationError, match="disagreeing evidence"):
        GeneratedClaim(
            text="재시도 횟수는 문서와 코드가 다르게 규정한다.",
            evidence_ids=("unit_1",),
            certainty="disputed",
        )

    claim = GeneratedClaim(
        text="운영 문서는 즉시 실패, 현재 정책 문서는 3회 재시도를 규정한다.",
        evidence_ids=("unit_doc", "unit_policy"),
        certainty="disputed",
    )
    assert claim.certainty == "disputed"


def test_generation_result_enforces_claim_cardinality() -> None:
    result = _result(
        GeneratedClaim(text="첫 주장", evidence_ids=("unit_1",), certainty="supported"),
        GeneratedClaim(text="둘째 주장", evidence_ids=("unit_1",), certainty="supported"),
    )

    with pytest.raises(KipValidationError, match="claim count"):
        validate_generation_result(result, allowed_evidence_ids=("unit_1",), max_claims=1)


def test_generation_usage_requires_consistent_token_total() -> None:
    with pytest.raises(ValidationError, match="total_tokens"):
        GenerationUsage(input_tokens=10, output_tokens=4, total_tokens=13)
