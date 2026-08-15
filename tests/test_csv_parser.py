from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from kip.adapters.parsers import csv_table as csv_table_module
from kip.adapters.parsers.csv_table import CsvTableParser
from kip.adapters.parsers.registry import ParserRegistry
from kip.errors import ParserError
from kip.settings import Settings


def _rows_from_body(body: str, delimiter: str = ",") -> list[list[str]]:
    return list(csv.reader(io.StringIO(body), delimiter=delimiter))


def test_csv_utf8_quoted_commas_and_newlines_round_trip(tmp_path: Path) -> None:
    # Given a UTF-8 CSV with a quoted field containing a comma and a newline.
    path = tmp_path / "quoted.csv"
    path.write_text(
        '이름,나이,메모\n"홍,길동",30,"여러줄\n메모"\n김철수,25,별일없음\n',
        encoding="utf-8",
    )

    # When it is parsed.
    extraction, units = CsvTableParser().parse(
        path, artifact_id="art_csv", document_id="doc_csv", acl_scopes=["workspace:default"]
    )

    # Then the quoted comma and embedded newline survive a round trip.
    assert extraction.status == "succeeded"
    assert extraction.quality_score == 1.0
    assert extraction.warnings == []
    assert len(units) == 1
    parsed_rows = _rows_from_body(units[0].body)
    assert parsed_rows == [
        ["이름", "나이", "메모"],
        ["홍,길동", "30", "여러줄\n메모"],
        ["김철수", "25", "별일없음"],
    ]
    assert units[0].metadata["columns"] == ["이름", "나이", "메모"]
    assert units[0].metadata["delimiter"] == ","
    assert units[0].locator.type == "csv_rows"
    assert units[0].locator.data == {"start_row": 2, "end_row": 3}


def test_csv_decodes_cp949_korean_with_informational_warning(tmp_path: Path) -> None:
    # Given a CP949-encoded Korean CSV export (the reported worst-case defect).
    path = tmp_path / "legacy.csv"
    path.write_bytes("이름,나이\n홍길동,30\n".encode("cp949"))

    # When it is parsed.
    extraction, units = CsvTableParser().parse(
        path, artifact_id="art_cp949", document_id="doc_cp949", acl_scopes=["workspace:default"]
    )

    # Then it decodes correctly at full quality with an informational warning.
    assert extraction.status == "succeeded"
    assert extraction.quality_score == 1.0
    assert extraction.warnings == ["decoded as cp949"]
    assert units[0].metadata["encoding"] == "cp949"
    assert units[0].metadata["columns"] == ["이름", "나이"]
    parsed_rows = _rows_from_body(units[0].body)
    assert parsed_rows == [["이름", "나이"], ["홍길동", "30"]]


def test_csv_strips_utf8_bom_from_first_column_name(tmp_path: Path) -> None:
    # Given a UTF-8 CSV with a leading byte-order mark.
    path = tmp_path / "bom.csv"
    path.write_bytes(b"\xef\xbb\xbf" + b"name,age\nAlice,30\n")

    # When it is parsed.
    extraction, units = CsvTableParser().parse(
        path, artifact_id="art_bom", document_id="doc_bom", acl_scopes=["workspace:default"]
    )

    # Then the first column name is clean, not prefixed with a stray BOM glyph.
    assert extraction.status == "succeeded"
    assert units[0].metadata["columns"] == ["name", "age"]
    assert units[0].metadata["columns"][0] == "name"


def test_csv_undecodable_binary_degrades_without_crashing(tmp_path: Path) -> None:
    # Given bytes with a raw NUL and content that fails UTF-8 and CP949 strict decoding.
    raw = bytes([0x80, 0x81, 0xFE, 0xFF, 0x00, 0x41]) * 20
    path = tmp_path / "garbage.csv"
    path.write_bytes(raw)

    # When it is parsed.
    extraction, _units = CsvTableParser().parse(
        path, artifact_id="art_bad", document_id="doc_bad", acl_scopes=["workspace:default"]
    )

    # Then it degrades to a machine-readable partial result instead of crashing.
    assert extraction.status == "partial"
    assert extraction.quality_score < 1.0
    assert any(warning.startswith("ENCODING_UNCERTAIN:") for warning in extraction.warnings)


def test_csv_row_locators_are_correct_across_chunk_boundaries(tmp_path: Path) -> None:
    # Given a CSV with enough rows to force multiple ~small chunks.
    path = tmp_path / "many_rows.csv"
    lines = ["id,value"] + [f"{i},row-{i}" for i in range(1, 21)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # When it is parsed with a small per-unit character budget.
    extraction, units = CsvTableParser(max_chars_per_unit=40).parse(
        path, artifact_id="art_chunks", document_id="doc_chunks", acl_scopes=["workspace:default"]
    )

    # Then row ranges are contiguous, 1-indexed with header=row 1, and cover
    # every data row exactly once with no gaps or overlaps.
    assert extraction.status == "succeeded"
    assert len(units) > 1
    ranges = [(unit.locator.data["start_row"], unit.locator.data["end_row"]) for unit in units]
    assert ranges[0][0] == 2
    covered_rows: list[int] = []
    for start, end in ranges:
        assert start <= end
        covered_rows.extend(range(start, end + 1))
    assert covered_rows == list(range(2, 22))
    for index, (_start, end) in enumerate(ranges[:-1]):
        assert ranges[index + 1][0] == end + 1
    # And every chunk's serialized body still round-trips to real CSV rows.
    for unit in units:
        parsed = _rows_from_body(unit.body)
        assert parsed[0] == ["id", "value"]


def test_csv_flags_partial_table_metadata_only_when_chunked(tmp_path: Path) -> None:
    # Given a small single-chunk CSV and a larger CSV forced into multiple
    # chunks by a small per-unit character budget.
    small_path = tmp_path / "small.csv"
    small_path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    large_path = tmp_path / "large.csv"
    lines = ["id,value"] + [f"{i},row-{i}" for i in range(1, 21)]
    large_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # When each is parsed.
    _small_extraction, small_units = CsvTableParser().parse(
        small_path, artifact_id="art_small", document_id="doc_small", acl_scopes=["workspace:default"]
    )
    _large_extraction, large_units = CsvTableParser(max_chars_per_unit=40).parse(
        large_path, artifact_id="art_large", document_id="doc_large", acl_scopes=["workspace:default"]
    )

    # Then only the file actually split across multiple chunks is flagged -
    # a numeric/aggregate question answered from just one of its chunks
    # (e.g. a total-row chunk) could otherwise look like it covers the
    # whole table when most line items are in a different chunk.
    assert len(small_units) == 1
    assert small_units[0].metadata["csv_partial_table"] is False
    assert small_units[0].metadata["csv_total_row_count"] == 2
    assert len(large_units) > 1
    assert all(unit.metadata["csv_partial_table"] is True for unit in large_units)
    assert all(unit.metadata["csv_total_row_count"] == 20 for unit in large_units)


def test_csv_sniffs_semicolon_delimiter(tmp_path: Path) -> None:
    # Given a semicolon-delimited CSV.
    path = tmp_path / "semicolon.csv"
    path.write_text("a;b;c\n1;2;3\n4;5;6\n", encoding="utf-8")

    # When it is parsed.
    extraction, units = CsvTableParser().parse(
        path, artifact_id="art_semi", document_id="doc_semi", acl_scopes=["workspace:default"]
    )

    # Then the semicolon delimiter is detected and rows split correctly.
    assert extraction.status == "succeeded"
    assert units[0].metadata["delimiter"] == ";"
    assert units[0].metadata["columns"] == ["a", "b", "c"]
    parsed_rows = _rows_from_body(units[0].body, delimiter=";")
    assert parsed_rows == [["a", "b", "c"], ["1", "2", "3"], ["4", "5", "6"]]


def test_csv_sniffs_tab_delimiter(tmp_path: Path) -> None:
    # Given a tab-delimited CSV.
    path = tmp_path / "tabbed.csv"
    path.write_text("a\tb\tc\n1\t2\t3\n4\t5\t6\n", encoding="utf-8")

    # When it is parsed.
    extraction, units = CsvTableParser().parse(
        path, artifact_id="art_tab", document_id="doc_tab", acl_scopes=["workspace:default"]
    )

    # Then the tab delimiter is detected and rows split correctly.
    assert extraction.status == "succeeded"
    assert units[0].metadata["delimiter"] == "\t"
    parsed_rows = _rows_from_body(units[0].body, delimiter="\t")
    assert parsed_rows == [["a", "b", "c"], ["1", "2", "3"], ["4", "5", "6"]]


def test_csv_ragged_rows_warn_without_failing_the_file(tmp_path: Path) -> None:
    # Given a CSV where some data rows have a different column count than the header.
    path = tmp_path / "ragged.csv"
    path.write_text("a,b,c\n1,2,3\n4,5\n6,7,8,9\n", encoding="utf-8")

    # When it is parsed.
    extraction, units = CsvTableParser().parse(
        path, artifact_id="art_ragged", document_id="doc_ragged", acl_scopes=["workspace:default"]
    )

    # Then the file still parses (best effort) with a warning, not a failure,
    # and quality is folded down below the clean-file 1.0 ceiling so a
    # mangled CSV is not reported as fully trustworthy alongside its own
    # "partial" status.
    assert extraction.status == "partial"
    assert any("ragged rows" in warning for warning in extraction.warnings)
    assert extraction.quality_score < 1.0
    assert len(units) == 1
    parsed_rows = _rows_from_body(units[0].body)
    assert parsed_rows[-2] == ["4", "5"]
    assert parsed_rows[-1] == ["6", "7", "8", "9"]


def test_csv_normalizes_classic_mac_carriage_return_only_line_endings(tmp_path: Path) -> None:
    # Given a CSV using classic Mac (\r-only) line endings. io.StringIO,
    # which csv.reader iterates over, does not split lines on a bare \r,
    # so without normalization this would collapse into one row or crash.
    path = tmp_path / "classic_mac.csv"
    path.write_bytes(b"a,b,c\r1,2,3\r")

    # When it is parsed.
    extraction, units = CsvTableParser().parse(
        path, artifact_id="art_cr", document_id="doc_cr", acl_scopes=["workspace:default"]
    )

    # Then the file parses into the expected header and single data row.
    assert extraction.status == "succeeded"
    assert len(units) == 1
    parsed_rows = _rows_from_body(units[0].body)
    assert parsed_rows == [["a", "b", "c"], ["1", "2", "3"]]
    assert units[0].metadata["columns"] == ["a", "b", "c"]


def test_csv_rejects_files_over_the_size_backstop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a file over a (reduced-for-testing) hard byte-size cap - a
    # defense-in-depth backstop, since CSV has no structural zip-bomb-style
    # guard the way XlsxShallowParser does.
    monkeypatch.setattr(csv_table_module, "_MAX_FILE_BYTES", 16)
    path = tmp_path / "oversized.csv"
    path.write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")

    # When it is parsed.
    # Then it fails cleanly as a typed ParserError before reading the file.
    with pytest.raises(ParserError, match="too large"):
        CsvTableParser().parse(
            path, artifact_id="art_big", document_id="doc_big", acl_scopes=["workspace:default"]
        )


def test_csv_recovers_delimiter_past_a_leading_comment_line(tmp_path: Path) -> None:
    # Given a semicolon-delimited CSV with a leading "#"-prefixed metadata
    # line, the kind of leading comment real export tools often prepend.
    # csv.Sniffer's frequency heuristic cannot find a consistent delimiter
    # across the whole sample (the comment line has none of the candidate
    # delimiters) and raises - before this fix, the code fell straight back
    # to the default ",", which does not appear anywhere in the actual data,
    # so every row parsed into a single field with a consistent column count
    # and the ragged-row check never tripped. That reported
    # status="succeeded"/quality=1.0 while every value was silently merged
    # into one field.
    path = tmp_path / "commented.csv"
    path.write_text(
        "# generated 2026-08-15 by export tool\na;b;c\n1;2;3\n4;5;6\n",
        encoding="utf-8",
    )

    # When it is parsed.
    extraction, units = CsvTableParser().parse(
        path, artifact_id="art_comment", document_id="doc_comment", acl_scopes=["workspace:default"]
    )

    # Then the real semicolon delimiter is recovered and each data row
    # actually splits into 3 fields (visible as a ragged-row warning against
    # the 1-field comment "header", not a silent single-field collapse).
    assert units[0].metadata["delimiter"] == ";"
    parsed_rows = _rows_from_body(units[0].body, delimiter=";")
    assert parsed_rows[1:] == [["a", "b", "c"], ["1", "2", "3"], ["4", "5", "6"]]
    assert extraction.status == "partial"
    assert any("ragged rows" in warning for warning in extraction.warnings)


def test_csv_routes_to_csv_table_parser_ahead_of_plain_text(tmp_path: Path) -> None:
    # Given the production parser registry.
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={"search": {"semantic_enabled": False}},
        environment="test",
        workspace="default",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )
    registry = ParserRegistry.from_settings(settings)

    # When resolving a parser for a .csv path.
    parser = registry.find(tmp_path / "data.csv")

    # Then the structural CSV parser is selected, not the plain-text parser.
    assert parser.name == "csv-table"
    assert registry.representation_role(".csv") == "workbook"
