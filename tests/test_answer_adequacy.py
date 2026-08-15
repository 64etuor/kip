from __future__ import annotations

from kip.application.answer_adequacy import prepare_answer_evidence
from kip.application.answers import assemble_answer
from kip.domain.models import (
    AnswerRequest,
    ContentUnit,
    EvidenceLocator,
    EvidenceRead,
)


def _evidence(
    unit_id: str,
    body: str,
    *,
    document_id: str,
) -> EvidenceRead:
    return EvidenceRead(
        unit=ContentUnit(
            id=unit_id,
            extraction_id=f"ext_{unit_id}",
            document_id=document_id,
            artifact_id=f"art_{unit_id}",
            ordinal=0,
            unit_type="text_document",
            body=body,
            body_normalized=body,
            lexical_text=body,
            locator=EvidenceLocator(
                type="text_line_range",
                data={"start_line": 1, "end_line": 1},
            ),
        ),
        source_uri=f"file:///{unit_id}.txt",
        indexed_source_sha256=unit_id * 8,
        current_source_sha256=unit_id * 8,
        source_changed_since_index=False,
    )


def _csv_evidence(
    unit_id: str,
    body: str,
    *,
    document_id: str,
    csv_partial_table: bool,
) -> EvidenceRead:
    return EvidenceRead(
        unit=ContentUnit(
            id=unit_id,
            extraction_id=f"ext_{unit_id}",
            document_id=document_id,
            artifact_id=f"art_{unit_id}",
            ordinal=0,
            unit_type="csv_rows",
            body=body,
            body_normalized=body,
            lexical_text=body,
            locator=EvidenceLocator(
                type="csv_rows",
                data={"start_row": 2, "end_row": 3},
            ),
            metadata={"csv_partial_table": csv_partial_table, "csv_total_row_count": 40},
        ),
        source_uri=f"file:///{unit_id}.csv",
        indexed_source_sha256=unit_id * 8,
        current_source_sha256=unit_id * 8,
        source_changed_since_index=False,
    )


def test_answer_refuses_when_explicit_identifier_is_absent() -> None:
    # Given generic approval evidence that does not contain the requested ID.
    evidence = _evidence(
        "generic",
        "승인 상태는 최종 확인 후 기록한다.",
        document_id="doc_generic",
    )

    # When the user asks about an unknown explicit identifier.
    response = assemble_answer(
        AnswerRequest(query="ZX-999 승인 상태가 뭐야?"),
        [evidence],
        had_stale_evidence=False,
    )

    # Then incidental approval words cannot produce an answer.
    assert response.refused is True
    assert response.refusal_reason == "answer_not_present"
    assert response.citations == []


def test_answer_refuses_numeric_intent_without_a_value() -> None:
    # Given related policy text that contains no rate or other numeric value.
    evidence = _evidence(
        "policy",
        "납품 지연 배상 기준은 계약서에 따른다.",
        document_id="doc_policy",
    )

    # When a value-bearing answer is requested.
    response = assemble_answer(
        AnswerRequest(query="납품 지연 배상률은 얼마야?"),
        [evidence],
        had_stale_evidence=False,
    )

    # Then related words alone are not treated as the requested answer.
    assert response.refused is True
    assert response.refusal_reason == "answer_not_present"
    assert response.citations == []


def test_answer_requests_clarification_for_generic_multi_document_question() -> None:
    # Given two independently relevant approval policies.
    evidence = [
        _evidence(
            "supplier",
            "협력업체 승인 기준은 평가 점수 70점 이상이다.",
            document_id="doc_supplier",
        ),
        _evidence(
            "expense",
            "비용 승인 기준은 부서장 결재 완료이다.",
            document_id="doc_expense",
        ),
    ]

    # When the question names no subject that selects one policy.
    response = assemble_answer(
        AnswerRequest(query="승인 기준이 뭐야?"),
        evidence,
        had_stale_evidence=False,
    )

    # Then KIP asks for scope instead of choosing the first document.
    assert response.refused is True
    assert response.refusal_reason == "clarification_required"
    assert response.citations == []


def test_answer_refuses_when_document_anchor_matches_but_question_focus_is_absent() -> None:
    # Given evidence that names the requested form but not the requested fact.
    evidence = _evidence(
        "qualification",
        "SEKR-QMS-W902-F01 자격인증 평가 Report 작성 절차를 설명한다.",
        document_id="doc_qualification",
    )

    # When a different fact is asked through the matching document anchor.
    response = assemble_answer(
        AnswerRequest(
            query="SEKR-QMS-W902-F01 자격인증 평가 Report의 탄소배출량 담당자는 누구인가?"
        ),
        [evidence],
        had_stale_evidence=False,
    )

    # Then the anchor words cannot stand in for an answer-bearing passage.
    assert response.refused is True
    assert response.refusal_reason == "answer_not_present"
    assert response.citations == []


def test_answer_refuses_when_only_a_generic_focus_term_matches() -> None:
    evidence = _evidence(
        "qualification",
        "SEKR-QMS-W902-F01 자격인증 평가 Report의 일반 점수 계산 담당자를 정한다.",
        document_id="doc_qualification",
    )

    response = assemble_answer(
        AnswerRequest(
            query="SEKR-QMS-W902-F01 자격인증 평가 Report의 탄소배출량 계산 담당자는 누구인가?"
        ),
        [evidence],
        had_stale_evidence=False,
    )

    assert response.refused is True
    assert response.refusal_reason == "answer_not_present"
    assert response.citations == []


def test_answer_requests_clarification_for_short_multi_document_topic() -> None:
    # Given several independently relevant qualification documents.
    evidence = [
        _evidence(
            "qualification-policy",
            "자격인증 평가 절차와 등급을 정의한다.",
            document_id="doc_qualification_policy",
        ),
        _evidence(
            "qualification-report",
            "자격인증 평가 Report 작성 방법을 정의한다.",
            document_id="doc_qualification_report",
        ),
    ]

    # When the query is only a short topic and does not select a document.
    response = assemble_answer(
        AnswerRequest(query="자격인증 평가"),
        evidence,
        had_stale_evidence=False,
    )

    # Then the system asks for scope rather than selecting the first extract.
    assert response.refused is True
    assert response.refusal_reason == "clarification_required"
    assert response.citations == []


def test_answer_refuses_numeric_question_answered_from_a_partial_csv_chunk() -> None:
    # Given a CSV total-row chunk (CsvTableParser flags csv_partial_table
    # when the source file was split into multiple row chunks - this one
    # chunk alone is not the full table).
    evidence = _csv_evidence(
        "csv-total",
        "합계\n1,200,000",
        document_id="doc_expense_csv",
        csv_partial_table=True,
    )

    # When a numeric/aggregate question cites only that partial chunk.
    prepared = prepare_answer_evidence(
        AnswerRequest(query="비용 합계 얼마야?"),
        [evidence],
        had_stale_evidence=False,
        apply_lexical_gate=False,
    )

    # Then the answer refuses instead of trusting one chunk for the total,
    # unlike xlsx's exact_xlsx_read_required this is CSV-specific.
    assert prepared.refusal is not None
    assert prepared.refusal.refused is True
    assert prepared.refusal.refusal_reason == "csv_full_table_required"
    assert prepared.evidence == ()


def test_answer_does_not_require_full_csv_read_for_a_single_chunk_file() -> None:
    # Given a CSV that fit entirely in one chunk (csv_partial_table is
    # False - the cited unit's body already is the whole table).
    evidence = _csv_evidence(
        "csv-total",
        "합계\n1,200,000",
        document_id="doc_expense_csv",
        csv_partial_table=False,
    )

    # When a numeric/aggregate question cites that complete chunk.
    prepared = prepare_answer_evidence(
        AnswerRequest(query="비용 합계 얼마야?"),
        [evidence],
        had_stale_evidence=False,
        apply_lexical_gate=False,
    )

    # Then the CSV-specific refusal does not trigger.
    assert prepared.refusal is None
    assert [item.unit.id for item in prepared.evidence] == ["csv-total"]
