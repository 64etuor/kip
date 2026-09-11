from unicodedata import normalize

import pytest

from kip.domain.file_references import (
    file_references,
    has_unresolved_file_reference,
    without_references,
)


@pytest.mark.parametrize("left,right", [("", ""), ('"', '"'), ("'", "'"), ("`", "`"), ("\u201c", "\u201d"), ("\u2018", "\u2019")])
def test_filename_delimiters_and_exclusion_share_one_parse(left, right):
    query = f"{left}범위 안내.txt{right}를 제외하고 다른 자료의 기한은?"
    refs = file_references(query, ["범위 안내.txt"])
    assert len(refs) == 1 and refs[0].excluded
    assert not has_unresolved_file_reference(query, refs)
    assert without_references(query, refs, excluded_only=True) == "다른 자료의 기한은?"


def test_longest_name_owns_overlapping_reference():
    query = '"보고서.txt 최종.txt"의 담당자는?'
    refs = file_references(query, ["보고서.txt", "최종.txt", "보고서.txt 최종.txt"])
    assert [ref.name for ref in refs] == ["보고서.txt 최종.txt"]
    assert without_references(query, refs) == "담당자는?"


@pytest.mark.parametrize("query", ['"없는 파일.txt"의 기한은?', '`없는 파일.txt` 기한은?', '없는파일.txt 기한은?'])
def test_unavailable_named_file_cannot_become_an_unscoped_question(query):
    refs = file_references(query, ["다른파일.txt"])
    assert has_unresolved_file_reference(query, refs)


def test_unicode_names_and_literal_punctuation():
    query = normalize("NFD", '"예산[1]_%자료.txt" 금액은?')
    refs = file_references(query, ["예산[1]_%자료.txt"])
    assert len(refs) == 1
    assert not has_unresolved_file_reference(query, refs)


@pytest.mark.parametrize("query", ["안내.txt 제외 대상은 누구인가?", "안내.txt 제외한도는 얼마야?"])
def test_exclusion_heading_is_not_an_exclusion_directive(query):
    refs = file_references(query, ["안내.txt"])
    assert len(refs) == 1 and not refs[0].excluded


def test_a_filename_prefix_is_not_the_requested_backup_file():
    query = '안내.txt.bak 최종 승인일은 언제인가?'
    refs = file_references(query, ["안내.txt"])
    assert refs == ()
    assert has_unresolved_file_reference(query, refs)


def test_long_non_filename_token_does_not_need_substring_retries():
    assert not has_unresolved_file_reference("x" * 50000, ())


def test_unresolved_detection_covers_operator_configured_extensions():
    from kip.domain.file_references import unresolved_file_tokens

    query = '"대상.png" 최종 승인일은 언제야?'
    assert unresolved_file_tokens(query, ()) == []
    assert unresolved_file_tokens(query, (), extensions=[".PNG"]) == ["대상.png"]


@pytest.mark.parametrize("query", [
    "https://intranet.example.com/guide.pdf 에 따르면 제출기한은 언제야?",
    "file://nas/team/report.json 참고, 제출기한은?",
])
def test_urls_are_context_not_file_requests(query):
    from kip.domain.file_references import candidate_basenames, unresolved_file_tokens

    assert unresolved_file_tokens(query, ()) == []
    assert not any(name.endswith((".pdf", ".json")) for name in candidate_basenames(query))


def test_candidate_basenames_cover_quotes_particles_and_preceding_words():
    from kip.domain.file_references import candidate_basenames

    names = candidate_basenames('2분기 범위 안내.txt의 담당자와 "최종 보고서.pdf"는?')
    assert "안내.txt" in names
    assert "범위 안내.txt" in names
    assert "2분기 범위 안내.txt" in names
    assert "최종 보고서.pdf" in names
    assert not any(name.startswith(('"', "'")) for name in names)


@pytest.mark.parametrize("query", ["3.8.1 릴리즈 노트", "평점 3.5 는?", '"승인" 관련 문서 찾아줘', "www.example.com/보고서.pdf 요약"])
def test_versions_decimals_quotes_and_urls_are_not_file_requests(query):
    from kip.domain.file_references import looks_like_file_request

    assert not looks_like_file_request(query)


def test_extension_tokens_are_file_requests():
    from kip.domain.file_references import looks_like_file_request

    assert looks_like_file_request("회의록(최종).txt 담당자는?")
    assert looks_like_file_request("REPORT.PDF 요약")
