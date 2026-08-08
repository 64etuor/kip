from __future__ import annotations

from kip.evaluation.answers import AnswerReview, ReviewedClaim, evaluate_answer


def test_answer_metrics_measure_grounding_completeness_and_citations() -> None:
    # Given a reviewed answer with one unsupported and one missing expected claim
    review = AnswerReview(
        case_id="GQ-ANSWER-001",
        expected_claims=("approval", "deadline"),
        claims=(
            ReviewedClaim(
                id="claim-1",
                expected_claim="approval",
                supported_by_evidence=True,
                citation_locator_correct=True,
            ),
            ReviewedClaim(
                id="claim-2",
                supported_by_evidence=False,
                citation_locator_correct=False,
            ),
        ),
    )

    # When deterministic answer quality is evaluated
    result = evaluate_answer(review)

    # Then each dimension reflects only reviewed evidence
    assert result.groundedness == 0.5
    assert result.completeness == 0.5
    assert result.citation_accuracy == 0.5
    assert result.unsupported_claim_count == 1


def test_answer_metrics_measure_safe_refusal() -> None:
    # Given a question whose reviewed outcome requires refusal
    review = AnswerReview(
        case_id="GQ-ANSWER-002",
        expected_refusal=True,
        refused=True,
    )

    # When answer quality is evaluated
    result = evaluate_answer(review)

    # Then refusal is measured without inventing claim scores
    assert result.refusal_accuracy == 1.0
    assert result.groundedness is None
    assert result.citation_accuracy is None


def test_answer_metrics_remain_unmeasured_without_review_annotations() -> None:
    # Given an answer case with no reviewed claims, citations, or refusal expectation
    review = AnswerReview(case_id="GQ-ANSWER-003")

    # When answer quality is evaluated
    result = evaluate_answer(review)

    # Then missing evidence is not converted into a passing score
    assert result.groundedness is None
    assert result.completeness is None
    assert result.citation_accuracy is None
    assert result.refusal_accuracy is None
