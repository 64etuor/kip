from __future__ import annotations

import os
import unicodedata

import pytest

from kip.domain.models import AnswerRequest, ContextRequest, SearchRequest


@pytest.mark.parametrize("query", [
    "별다람연구사업의", "별다람연구사업에서", "별다람연구사업으로",
    "별다람연구사업은", "별다람연구사업을",
    unicodedata.normalize("NFD", "별다람연구사업의"),
])
def test_korean_particles_do_not_hide_an_indexed_subject(test_container, query):
    source = test_container.settings.project_root / "source" / "brief.txt"
    source.write_text("별다람연구사업 추진 안내. 담당 부서는 연구팀이다.")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    hits = test_container.application.retrieval.search(context, SearchRequest(query=query))

    assert hits and hits[0].metadata["file_name"] == "brief.txt"
    outsider = context.model_copy(update={"acl_scopes": []})
    assert test_container.application.retrieval.search(outsider, SearchRequest(query=query)) == []
    assert test_container.application.retrieval.search(
        context, SearchRequest(query="없는별조약사업의")
    ) == []


@pytest.mark.parametrize("file_name", ["ADM-713_의결문.txt", "한글 파일명.txt"])
def test_exact_filename_answer_reopens_the_named_evidence(test_container, file_name):
    source = test_container.settings.project_root / "source" / file_name
    source.write_text("사업의 범위는 현장 조사이다. 승인되지 않은 검토용 초안이다.")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    response = test_container.application.answering.answer(context, AnswerRequest(query=file_name))

    assert not response.refused
    assert response.answer == source.read_text()
    assert len(response.citations) == 1
    assert response.citations[0].source_changed_since_index is False
    assert response.citations[0].source_verification == "stat"

    unknown = test_container.application.answering.answer(
        context, AnswerRequest(query=f"{file_name} 최종 승인일이 언제야?")
    )
    assert unknown.refused
    assert unknown.refusal_reason == "answer_not_present"
    assert "지정한 문서는 찾았지만" in unknown.answer

    scoped = test_container.application.answering.answer(
        context, AnswerRequest(query=f"{file_name}의 사업 범위는 무엇인가?")
    )
    assert not scoped.refused
    assert scoped.answer == source.read_text()
    assert len(scoped.citations) == 1


@pytest.mark.parametrize("suffix", ["?", "은?", "의", " ?", "을 보여줘"])
def test_named_file_with_punctuation_or_particle_returns_the_document(test_container, suffix):
    source = test_container.settings.project_root / "source" / "범위 안내.txt"
    source.write_text("사업 범위는 현장 조사이다.")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    response = test_container.application.answering.answer(
        context, AnswerRequest(query=f"범위 안내.txt{suffix}")
    )

    assert not response.refused, response.answer
    assert response.answer == source.read_text()
    assert len(response.citations) == 1


def test_question_naming_two_different_files_is_a_comparison_not_ambiguity(test_container):
    source = test_container.settings.project_root / "source"
    (source / "범위 안내.txt").write_text("범위 담당자는 기획팀이다.")
    (source / "예산 안내.txt").write_text("예산 담당자는 회계팀이다.")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    response = test_container.application.answering.answer(
        context, AnswerRequest(query="범위 안내.txt와 예산 안내.txt의 담당자는?")
    )

    # Extracts quote one document by design; combining sources is the
    # generation path's job. The point is that two names are not a duplicate.
    assert not response.refused, response.answer
    assert response.refusal_reason is None
    assert len(response.citations) == 1
    assert response.citations[0].source_uri.rsplit("/", 1)[1] in {
        "%EB%B2%94%EC%9C%84%20%EC%95%88%EB%82%B4.txt", "%EC%98%88%EC%82%B0%20%EC%95%88%EB%82%B4.txt",
    }
    assert response.answer in {"범위 담당자는 기획팀이다.", "예산 담당자는 회계팀이다."}


def test_excluding_a_named_file_does_not_scope_the_answer_to_it(test_container):
    source = test_container.settings.project_root / "source"
    (source / "정산_안내.txt").write_text("정산 담당자는 회계팀이다.")
    (source / "메모.txt").write_text("다른 자료 담당자는 연구팀이다.")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    response = test_container.application.answering.answer(
        context, AnswerRequest(query="정산_안내.txt 말고 다른 자료의 담당자는?")
    )

    assert not response.refused, response.answer
    assert "연구팀" in response.answer


def test_exclusion_words_are_not_subject_keywords(test_container):
    source = test_container.settings.project_root / "source"
    (source / "메모.md").write_text("메모 담당자는 연구팀이다.")
    (source / "정산_안내.txt").write_text("A과제 2분기 정산 증빙 제출기한은 2026년 8월 15일이다.")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    response = test_container.application.answering.answer(
        context, AnswerRequest(query="메모.md 말고 다른 자료의 제출기한은?")
    )

    assert not response.refused, response.answer
    assert response.answer == (source / "정산_안내.txt").read_text()


def test_excluded_duplicate_name_does_not_require_disambiguation(test_container):
    source = test_container.settings.project_root / "source"
    for folder, body in (("a", "a 보고서 담당자는 기획팀이다."), ("b", "b 보고서 담당자는 회계팀이다.")):
        (source / folder).mkdir()
        (source / folder / "보고서.txt").write_text(body)
    (source / "메모.txt").write_text("다른 자료 담당자는 연구팀이다.")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    response = test_container.application.answering.answer(
        context, AnswerRequest(query="보고서.txt 말고 다른 자료의 담당자는?")
    )

    assert not response.refused, response.answer
    assert "연구팀" in response.answer


def test_question_naming_one_of_two_same_named_files_requires_disambiguation(test_container):
    source = test_container.settings.project_root / "source"
    for folder, body in (("a", "a 사업 범위는 현장 조사이다."), ("b", "b 사업 범위는 실내 검토이다.")):
        (source / folder).mkdir()
        (source / folder / "범위 안내.txt").write_text(body)
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    response = test_container.application.answering.answer(
        context, AnswerRequest(query="범위 안내.txt 사업 범위는 무엇인가?")
    )

    assert response.refused
    assert response.refusal_reason == "clarification_required"


@pytest.mark.parametrize("limit", [1, 5])
def test_identical_filenames_require_disambiguation(test_container, limit):
    source = test_container.settings.project_root / "source"
    for folder in ("a", "b"):
        (source / folder).mkdir()
        (source / folder / "사업 승인 의결문.txt").write_text(f"{folder} 사업 검토 문서")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    response = test_container.application.answering.answer(
        context, AnswerRequest(query="사업 승인 의결문.txt", limit=limit)
    )

    assert response.refused
    assert response.refusal_reason == "clarification_required"


@pytest.mark.parametrize("query", ["제출기한은 언제야?", "증빙 제출기한은 언제까지야", "제출기한이 언제인지 알려주세요"])
def test_interrogative_endings_do_not_hide_a_single_topic_answer(test_container, query):
    source = test_container.settings.project_root / "source" / "안내.txt"
    source.write_text("A과제 2분기 정산 증빙 제출기한은 2026년 8월 15일이다.")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    response = test_container.application.answering.answer(context, AnswerRequest(query=query))

    assert not response.refused
    assert response.answer == source.read_text()
    assert test_container.application.answering.answer(
        context, AnswerRequest(query="계약 체결일은 언제야?")
    ).refused


def test_document_title_counts_toward_question_relevance(test_container):
    from openpyxl import Workbook

    source = test_container.settings.project_root / "source" / "A과제_정산.xlsx"
    book = Workbook()
    book.active.title = "코드"
    book.active.append(["과제번호", "A-2026-001"])
    book.save(source)
    (source.parent / "B과제_메모.txt").write_text("B과제 담당자는 연구팀장이다.")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    workbook = test_container.application.answering.answer(
        context, AnswerRequest(query="A과제 과제번호가 뭐야?")
    )
    assert workbook.refusal_reason == "exact_xlsx_read_required"

    memo = test_container.application.answering.answer(
        context, AnswerRequest(query="B과제 담당자는 누구야?")
    )
    assert not memo.refused
    assert memo.answer == "B과제 담당자는 연구팀장이다."


def test_identical_copies_of_a_named_file_are_not_ambiguous(test_container):
    source = test_container.settings.project_root / "source"
    for folder in ("a", "b"):
        (source / folder).mkdir()
        (source / folder / "사업 승인 의결문.txt").write_text("동일한 사업 검토 문서")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    response = test_container.application.answering.answer(
        context, AnswerRequest(query="사업 승인 의결문.txt", limit=1)
    )

    assert not response.refused
    assert response.answer == "동일한 사업 검토 문서"
    assert len(response.citations) == 1


def test_filename_answer_cannot_bypass_deep_workbook_requirement(test_container):
    from openpyxl import Workbook

    source = test_container.settings.project_root / "source" / "예산.xlsx"
    book = Workbook()
    book.active.append(["항목", "금액"])
    book.active.append(["교육", 1300])
    book.save(source)
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    response = test_container.application.answering.answer(context, AnswerRequest(query="예산.xlsx"))

    assert response.refused
    assert response.refusal_reason == "exact_xlsx_read_required"


def test_search_context_and_read_expose_their_actual_verification(test_container):
    source = test_container.settings.project_root / "source" / "verification.txt"
    source.write_text("현장조사 안내 " * 1000)
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    hit = test_container.application.retrieval.search(context, SearchRequest(query="현장조사"))[0]
    assert hit.evidence_role == "discovery"
    assert hit.source_verification == "not_checked"
    test_container.settings.raw["search"]["context_item_max_chars"] = 1000
    pack = test_container.application.retrieval.context_bundle(
        context, ContextRequest(query="현장조사", max_chars=1000, limit=1)
    )
    item = pack.items[0]
    assert item.source_verification == "stat"
    assert item.body_truncated is True
    assert item.body == source.read_text()[:1000]
    read = test_container.application.evidence.read_unit(context, hit.unit_id)
    assert read.source_verification == "sha256"

    # Equal length and restored mtime can fool stat reuse, but exact read
    # must still hash the live source and detect different bytes.
    before = source.stat()
    source.write_text(source.read_text().replace("현장", "실내"))
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    changed = test_container.application.evidence.read_unit(context, hit.unit_id)
    assert changed.source_verification == "sha256"
    assert changed.source_changed_since_index is True


def test_missing_source_cannot_be_reported_as_hash_verified(test_container):
    source = test_container.settings.project_root / "source" / "missing.txt"
    source.write_text("근거확인 문서")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    hit = test_container.application.retrieval.search(context, SearchRequest(query="근거확인"))[0]
    source.unlink()

    read = test_container.application.evidence.read_unit(context, hit.unit_id)

    # An unreadable source compares nothing, so the field must stay unknown.
    # Reporting `true` here made an agent tell a user their legal document had
    # been changed after indexing, which nothing had established.
    assert read.source_verification == "unavailable"
    assert read.source_changed_since_index is None
