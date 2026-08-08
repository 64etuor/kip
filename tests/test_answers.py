from kip.application.answers import assemble_answer
from kip.domain.models import AnswerRequest, ContentUnit, EvidenceLocator, EvidenceRead


def _evidence(unit_id: str, body: str) -> EvidenceRead:
    return EvidenceRead(
        unit=ContentUnit(
            id=unit_id,
            extraction_id=f"ext_{unit_id}",
            artifact_id=f"art_{unit_id}",
            ordinal=0,
            unit_type="text_document",
            body=body,
            body_normalized=body,
            lexical_text=body,
            locator=EvidenceLocator(type="text_line_range", data={"start_line": 1, "end_line": 1}),
        ),
        source_uri=f"file:///{unit_id}.txt",
        indexed_source_sha256=unit_id * 8,
        current_source_sha256=unit_id * 8,
        source_changed_since_index=False,
    )


def test_approval_refusal_cites_only_evidence_about_the_named_subject() -> None:
    request = AnswerRequest(query="A과제 참여율 변경이 승인됐어?", limit=10)
    subject_evidence = _evidence(
        "subject",
        "A과제 참여율 조정이 논의되었다. 공식 승인 문서를 확인해야 한다.",
    )
    generic_policy = _evidence(
        "generic",
        "교육 참여율 변경은 담당자 승인이 필요하다. 변경 후 승인 기록을 보관한다.",
    )

    response = assemble_answer(
        request,
        [subject_evidence, generic_policy],
        had_stale_evidence=False,
    )

    assert [citation.unit_id for citation in response.citations] == ["subject"]
