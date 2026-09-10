from __future__ import annotations

import pytest

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


@pytest.mark.parametrize("query", ["What is the total budget?", "인원은 몇 명?", "예산을 더하면?", "비용을 알려주세요"])
def test_shallow_workbook_is_not_answer_evidence_in_any_language(query: str) -> None:
    item = _evidence("sheet", "예산 인원 비용 budget headcount", document_id="budget")
    item.unit.locator = EvidenceLocator(type="xlsx_sheet", data={"sheet": "Budget"})
    prepared = prepare_answer_evidence(
        AnswerRequest(query=query), [item], had_stale_evidence=False, apply_lexical_gate=False,
    )
    assert not prepared.evidence
    assert prepared.refusal is not None
    assert prepared.refusal.refusal_reason == "exact_xlsx_read_required"
    assert prepared.refusal.citations[0].locator.data["sheet"] == "Budget"


def test_complete_evidence_is_not_blocked_by_shallow_workbook_discovery() -> None:
    sheet = _evidence("sheet", "budget", document_id="budget")
    sheet.unit.locator = EvidenceLocator(type="xlsx_sheet", data={"sheet": "Budget"})
    document = _evidence("policy", "The approved budget is 450000 won.", document_id="policy")
    prepared = prepare_answer_evidence(
        AnswerRequest(query="What is the approved budget?"), [sheet, document],
        had_stale_evidence=False, apply_lexical_gate=False,
    )
    assert prepared.refusal is None
    assert [item.unit.id for item in prepared.evidence] == ["policy"]


def test_partial_csv_requires_complete_coverage_independent_of_query_language() -> None:
    item = _csv_evidence("part", "Budget,450000", document_id="budget", csv_partial_table=True)
    prepared = prepare_answer_evidence(
        AnswerRequest(query="What is the total budget?"), [item],
        had_stale_evidence=False, apply_lexical_gate=False,
    )
    assert not prepared.evidence
    assert prepared.refusal is not None
    assert prepared.refusal.refusal_reason == "csv_full_table_required"


@pytest.mark.parametrize("second_start,second_end,allowed", [(4, 5, True), (5, 5, False), (3, 5, False)])
def test_csv_answer_requires_contiguous_nonoverlapping_rows(second_start, second_end, allowed):
    first = _csv_evidence("first", "Budget\n10\n20", document_id="budget", csv_partial_table=True)
    second = _csv_evidence("second", "Budget\n30\n40", document_id="budget", csv_partial_table=True)
    for item in (first, second):
        item.unit.artifact_id = "shared-artifact"
        item.unit.extraction_id = "shared-extraction"
        item.indexed_source_sha256 = "a" * 64
        item.current_source_sha256 = "a" * 64
        item.unit.metadata["csv_total_row_count"] = 4
    second.unit.locator.data = {"start_row": second_start, "end_row": second_end}
    prepared = prepare_answer_evidence(
        AnswerRequest(query="What is the budget?"), [first, second],
        had_stale_evidence=False, apply_lexical_gate=False,
    )
    assert bool(prepared.evidence) is allowed
    assert (prepared.refusal is None) is allowed


def test_complete_csv_must_fit_the_answer_context_budget():
    item = _csv_evidence("complete", "Budget,100\n" * 150, document_id="budget", csv_partial_table=False)
    prepared = prepare_answer_evidence(
        AnswerRequest(query="What is the total budget?", max_chars=1000), [item],
        had_stale_evidence=False, apply_lexical_gate=False,
    )
    assert prepared.refusal is not None
    assert prepared.refusal.refusal_reason == "csv_full_table_required"


def test_missing_csv_completeness_metadata_is_not_treated_as_a_complete_table():
    item = _csv_evidence("legacy", "Budget,100", document_id="budget", csv_partial_table=True)
    item.unit.metadata = {}
    prepared = prepare_answer_evidence(
        AnswerRequest(query="What is the total budget?"), [item],
        had_stale_evidence=False, apply_lexical_gate=False,
    )
    assert prepared.refusal is not None
    assert prepared.refusal.refusal_reason == "csv_full_table_required"


def test_naming_a_file_keeps_approved_ontology_evidence() -> None:
    document = _evidence("u1", "계약 상대방은 한빛전자이다.", document_id="ldoc_1")
    graph = _evidence("u3", "계약 상대방 관계 근거", document_id="ldoc_3").model_copy(
        update={"source_uri": "slack://workspace/channel/message-3"}
    )

    prepared = prepare_answer_evidence(
        AnswerRequest(query="u1.txt 계약 상대방은 누구인가?"),
        [document, graph],
        had_stale_evidence=False,
        ontology_evidence_ids={"u3"},
    )

    assert prepared.refusal is None
    assert [item.unit.id for item in prepared.evidence] == ["u1", "u3"]


def test_bare_named_file_keeps_approved_ontology_evidence() -> None:
    document = _evidence("u1", "계약 상대방은 한빛전자이다.", document_id="ldoc_1")
    graph = _evidence("u3", "계약 상대방 관계 근거", document_id="ldoc_3").model_copy(
        update={"source_uri": "slack://workspace/channel/message-3"}
    )

    prepared = prepare_answer_evidence(
        AnswerRequest(query="u1.txt"), [document, graph],
        had_stale_evidence=False, ontology_evidence_ids={"u3"},
    )

    assert prepared.refusal is None
    assert [item.unit.id for item in prepared.evidence] == ["u1", "u3"]


def test_named_file_scoping_applies_without_the_lexical_gate() -> None:
    named = _evidence("u1", "계약 상대방은 한빛전자이다.", document_id="ldoc_1")
    other = _evidence("u2", "계약 상대방은 다른 회사이다.", document_id="ldoc_2")

    prepared = prepare_answer_evidence(
        AnswerRequest(query="u1.txt 계약 상대방은 누구인가?"), [named, other],
        had_stale_evidence=False, apply_lexical_gate=False,
    )

    assert prepared.refusal is None
    assert [item.unit.id for item in prepared.evidence] == ["u1"]


def test_content_negation_is_not_a_file_exclusion() -> None:
    named = _evidence("q2", "정규직이 아닌 인력의 제출기한은 2026년 8월 15일이다.", document_id="ldoc_q2").model_copy(
        update={"source_uri": "file:///2분기정산.txt"}
    )
    other = _evidence("q1", "정규직 인력의 제출기한은 2026년 5월 10일이다.", document_id="ldoc_q1").model_copy(
        update={"source_uri": "file:///1분기정산.txt"}
    )

    prepared = prepare_answer_evidence(
        AnswerRequest(query="2분기정산.txt에서 정규직이 아닌 인력의 제출기한은?"),
        [other, named],
        had_stale_evidence=False,
    )

    assert prepared.refusal is None
    assert [item.unit.id for item in prepared.evidence] == ["q2"]


@pytest.mark.parametrize("query", ["u1.txt 말고 제출기한은?", "u1.txt를 제외하고 제출기한은?", "u1.txt 외에 제출기한은?"])
def test_adjacent_exclusion_drops_the_named_file(query: str) -> None:
    excluded = _evidence("u1", "제출기한은 2026년 5월 10일이다.", document_id="ldoc_1")
    other = _evidence("u2", "제출기한은 2026년 8월 15일이다.", document_id="ldoc_2")

    prepared = prepare_answer_evidence(
        AnswerRequest(query=query), [excluded, other], had_stale_evidence=False,
    )

    assert prepared.refusal is None
    assert [item.unit.id for item in prepared.evidence] == ["u2"]


@pytest.mark.parametrize("filename", ["회의록.txt", "협의안.txt", "금액표.txt", "ADM-713_의결문.txt"])
def test_bare_named_file_keeps_ontology_evidence_regardless_of_its_name(filename: str) -> None:
    document = _evidence("u1", "계약 상대방은 한빛전자이다.", document_id="ldoc_1").model_copy(
        update={"source_uri": f"file:///{filename}"}
    )
    graph = _evidence("u9", "계약 상대방 관계 근거", document_id="ldoc_9").model_copy(
        update={"source_uri": "slack://workspace/channel/message-9"}
    )

    prepared = prepare_answer_evidence(
        AnswerRequest(query=filename), [document, graph],
        had_stale_evidence=False, ontology_evidence_ids={"u9"},
    )

    assert prepared.refusal is None
    assert [item.unit.id for item in prepared.evidence] == ["u1", "u9"]


def test_extensionless_common_word_basename_is_not_a_scope_directive() -> None:
    memo = _evidence("m1", "메모 담당자는 연구팀이다.", document_id="ldoc_m").model_copy(
        update={"source_uri": "file:///메모"}
    )
    notice = _evidence("n1", "메모에 있는 제출기한은 2026년 8월 15일이다.", document_id="ldoc_n")

    # Without scoping, both documents pass through the ordinary lexical gate.
    prepared = prepare_answer_evidence(
        AnswerRequest(query="메모에 있는 제출기한은?"), [memo, notice], had_stale_evidence=False,
    )

    assert prepared.refusal is None
    assert [item.unit.id for item in prepared.evidence] == ["n1"]
    exact = prepare_answer_evidence(AnswerRequest(query="메모"), [memo, notice], had_stale_evidence=False)
    assert exact.refusal is None
    assert [item.unit.id for item in exact.evidence] == ["m1"]


@pytest.mark.parametrize("query", [
    "u1.txt 말고 u2.txt의 제출기한은?",
    "u1.txt를 제외하고 u2.txt의 제출기한은?",
    "u1.txt말고 제출기한은?",
])
def test_exclusion_then_another_named_or_remaining_file_answers(query: str) -> None:
    excluded = _evidence("u1", "제출기한은 2026년 5월 10일이다.", document_id="ldoc_1")
    other = _evidence("u2", "제출기한은 2026년 8월 15일이다.", document_id="ldoc_2")

    prepared = prepare_answer_evidence(
        AnswerRequest(query=query), [excluded, other], had_stale_evidence=False,
    )

    assert prepared.refusal is None, prepared.refusal
    assert [item.unit.id for item in prepared.evidence] == ["u2"]


def test_exclusion_heading_inside_the_document_is_not_a_scope_directive() -> None:
    notice = _evidence("s1", "정산 제외 대상은 계약직이다.", document_id="ldoc_s").model_copy(
        update={"source_uri": "file:///정산.txt"}
    )
    other = _evidence("s2", "다른 정산 담당자는 회계팀이다.", document_id="ldoc_o")

    prepared = prepare_answer_evidence(
        AnswerRequest(query="정산.txt 제외 대상은 누구야?"), [notice, other], had_stale_evidence=False,
    )

    assert prepared.refusal is None, prepared.refusal
    assert [item.unit.id for item in prepared.evidence] == ["s1"]


@pytest.mark.parametrize("query", [
    "정산.txt를 제외한 나머지 자료의 제출기한은?",
    "정산.txt 제외하면 제출기한은?",
    "정산.txt를 제외하여 제출기한은?",
    "정산.txt 제외, 다른 자료의 제출기한은?",
])
def test_inflected_exclusion_markers_do_not_become_required_keywords(query: str) -> None:
    excluded = _evidence("s1", "정산 제출기한은 2026년 5월 10일이다.", document_id="ldoc_s").model_copy(
        update={"source_uri": "file:///정산.txt"}
    )
    other = _evidence("s2", "나머지 자료 제출기한은 2026년 7월 7일이다.", document_id="ldoc_o")

    prepared = prepare_answer_evidence(
        AnswerRequest(query=query), [excluded, other], had_stale_evidence=False,
    )

    assert prepared.refusal is None, prepared.refusal
    assert [item.unit.id for item in prepared.evidence] == ["s2"]
