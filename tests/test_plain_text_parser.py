from __future__ import annotations

from pathlib import Path

from kip.adapters.parsers.plain import PlainTextParser, decode_text_bytes


def test_plain_text_clean_utf8_is_unchanged(tmp_path: Path) -> None:
    # Given a clean UTF-8 markdown file.
    path = tmp_path / "notes.md"
    path.write_text("# 근거 문서\n\n본문 내용", encoding="utf-8")

    # When it is parsed through the production adapter.
    extraction, units = PlainTextParser().parse(
        path, artifact_id="art_txt", document_id="doc_txt", acl_scopes=["workspace:default"]
    )

    # Then behavior is unchanged: one unit, full quality, no warnings.
    assert len(units) == 1
    assert extraction.status == "succeeded"
    assert extraction.quality_score == 1.0
    assert extraction.warnings == []
    assert units[0].metadata["encoding"] == "utf-8"
    assert units[0].body == "# 근거 문서\n\n본문 내용"


def test_plain_text_strips_utf8_bom_from_first_line(tmp_path: Path) -> None:
    # Given a UTF-8 file with a leading byte-order mark.
    path = tmp_path / "with_bom.txt"
    path.write_bytes(b"\xef\xbb\xbf" + "제목: 계약서".encode())

    # When it is parsed.
    extraction, units = PlainTextParser().parse(
        path, artifact_id="art_bom", document_id="doc_bom", acl_scopes=["workspace:default"]
    )

    # Then the BOM does not land as a literal character on the first line.
    assert units[0].body == "제목: 계약서"
    assert not units[0].body.startswith("﻿")
    assert extraction.status == "succeeded"
    assert extraction.quality_score == 1.0


def test_plain_text_decodes_cp949_korean_with_informational_warning(tmp_path: Path) -> None:
    # Given a CP949-encoded Korean text file (a common legacy Excel/Notepad export).
    path = tmp_path / "legacy.txt"
    path.write_bytes("협약 변경 승인 문서".encode("cp949"))

    # When it is parsed.
    extraction, units = PlainTextParser().parse(
        path, artifact_id="art_cp949", document_id="doc_cp949", acl_scopes=["workspace:default"]
    )

    # Then it decodes correctly (not mojibake) at full quality with an
    # informational, non-failing warning.
    assert units[0].body == "협약 변경 승인 문서"
    assert units[0].metadata["encoding"] == "cp949"
    assert extraction.status == "succeeded"
    assert extraction.quality_score == 1.0
    assert extraction.warnings == ["decoded as cp949"]


def test_plain_text_undecodable_binary_is_flagged_uncertain_with_reduced_quality(
    tmp_path: Path,
) -> None:
    # Given bytes that fail both UTF-8 strict and CP949 strict decoding.
    raw = bytes([0x80, 0x81, 0xFE, 0xFF, 0x41, 0x42, 0x43]) * 5
    path = tmp_path / "garbage.txt"
    path.write_bytes(raw)

    # When it is parsed.
    extraction, units = PlainTextParser().parse(
        path, artifact_id="art_bad", document_id="doc_bad", acl_scopes=["workspace:default"]
    )

    # Then it degrades instead of silently reporting full-quality success.
    assert extraction.status == "partial"
    assert extraction.quality_score == 0.4286
    assert extraction.warnings == [
        "ENCODING_UNCERTAIN: 57% replacement characters after utf-8/cp949 attempts"
    ]
    assert units[0].metadata["encoding"] == "utf-8"


def test_decode_text_bytes_empty_input_is_full_quality_success() -> None:
    # Given empty bytes (an edge case that must not divide by zero).
    decoded = decode_text_bytes(b"")

    # Then decoding trivially succeeds.
    assert decoded.text == ""
    assert decoded.status == "succeeded"
    assert decoded.quality == 1.0
    assert decoded.warnings == []
