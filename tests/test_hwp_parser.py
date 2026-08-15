from __future__ import annotations

import json
import sys
import zipfile
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from kip.adapters.parsers.hwp_broker import CommandParserConfig, HwpParserBroker
from kip.adapters.parsers.hwp_native import HwpNativeParser, split_text
from kip.adapters.parsers.registry import ParserRegistry
from kip.errors import ParserError
from kip.settings import Settings


def test_overlap_keeps_boundary_spanning_facts_in_one_chunk():
    fact = "제출기한은 8월 15일이다"
    filler_a = ("가" * 30 + "\n") * 3
    text = filler_a + fact + "\n" + ("나" * 30 + "\n") * 3
    without = split_text(text, max_chars=100, overlap_chars=0)
    with_overlap = split_text(text, max_chars=100, overlap_chars=40)

    assert any(fact in chunk for chunk in with_overlap)
    # The overlapping variant never loses text: concatenated coverage is intact.
    assert "".join(without) == text


def test_split_text_spans_report_true_offsets():
    from kip.adapters.parsers.hwp_native import split_text_spans

    text = "\n".join("줄" * 20 for _ in range(20))
    spans = split_text_spans(text, max_chars=100, overlap_chars=30)

    for start, chunk in spans:
        assert text[start : start + len(chunk)] == chunk
    assert spans[1][0] < spans[0][0] + len(spans[0][1])  # windows overlap


def test_split_text_preserves_order_without_dropping_long_lines():
    text = "첫 문단\n" + ("긴 문장 " * 20) + "\n마지막 문단"

    chunks = split_text(text, max_chars=40)

    assert len(chunks) > 1
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")
    assert all(len(chunk) <= 40 for chunk in chunks)


def test_native_parser_emits_bounded_evidence_units(monkeypatch, tmp_path):
    class FakeTable:
        def to_markdown(self):
            return "| 항목 | 값 |"

    class FakeReader:
        def __init__(self, filepath):
            self.filepath = filepath
            self.tables = [FakeTable()]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def extract_text(self):
            return "가나다 " * 40

        def get_images(self):
            return [object()]

    monkeypatch.setitem(sys.modules, "hwp_hwpx_parser", SimpleNamespace(Reader=FakeReader))
    path = tmp_path / "fixture.hwp"
    path.write_bytes(b"fixture")

    extraction, units = HwpNativeParser(max_chars_per_unit=40).parse(
        path,
        artifact_id="art_fixture",
        document_id="doc_fixture",
        acl_scopes=["workspace:default"],
    )

    # This FakeReader is a pure stand-in (no _get_reader()/_get_section_files()
    # etc.) so the section fail-safe correctly cannot verify a reconstruction
    # against it and falls back to section: None with a warning, rather than
    # ever guessing - see _reconstruct_section_spans in hwp_native.py.
    assert extraction.parser_name == "hwp-hwpx-parser"
    # The section label is best-effort enrichment: losing it is reported as a
    # warning but must NOT downgrade the extraction to "partial", which
    # operators read as "some document content is missing".
    assert extraction.status == "succeeded"
    assert extraction.warnings == [
        "SECTION_INDEX_UNAVAILABLE: per-section reconstruction did not "
        "verify against extract_text() output; section left as None"
    ]
    assert extraction.metadata["table_count"] == 1
    assert extraction.metadata["image_count"] == 1
    assert len(units) > 1
    assert all(len(unit.body) <= 40 for unit in units)
    assert all(unit.locator.type == "hwp_structure" for unit in units)
    assert all("section" in unit.locator.data for unit in units)
    assert all(unit.locator.data["section"] is None for unit in units)


def _fake_reader_class(text: str):
    class FakeReader:
        def __init__(self, filepath):
            self.filepath = filepath
            self.tables: list[object] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def extract_text(self):
            return text

        def get_images(self):
            return []

    return FakeReader


def test_native_parser_scores_a_clean_long_korean_document_near_the_ceiling(
    monkeypatch, tmp_path
):
    # Given a long, clean, fully-Hangul extraction. The FakeReader below has
    # no real per-section internals, so the section fail-safe adds exactly
    # one SECTION_INDEX_UNAVAILABLE warning (see
    # test_native_parser_emits_bounded_evidence_units) - the one warning
    # this test still expects.
    text = "계약 조건과 승인 절차를 명확히 기록한 문서입니다. " * 80
    monkeypatch.setitem(
        sys.modules, "hwp_hwpx_parser", SimpleNamespace(Reader=_fake_reader_class(text))
    )
    path = tmp_path / "clean.hwp"
    path.write_bytes(b"fixture")

    extraction, units = HwpNativeParser().parse(
        path,
        artifact_id="art_fixture",
        document_id="doc_fixture",
        acl_scopes=["workspace:default"],
    )

    # Then quality reflects the shared hwp_text_quality formula and stays >= 0.9,
    # matching the flat 0.95 the parser used before content-derived scoring,
    # even after the one section-fail-safe warning's penalty.
    assert extraction.status == "succeeded"
    assert extraction.warnings == [
        "SECTION_INDEX_UNAVAILABLE: per-section reconstruction did not "
        "verify against extract_text() output; section left as None"
    ]
    assert units
    assert extraction.quality_score >= 0.9


def test_native_parser_scores_garbled_replacement_riddled_text_lower_than_clean_text(
    monkeypatch, tmp_path
):
    # Given a short extraction dominated by decode-failure replacement characters.
    garbled = "�" * 60 + "가나다"
    monkeypatch.setitem(
        sys.modules, "hwp_hwpx_parser", SimpleNamespace(Reader=_fake_reader_class(garbled))
    )
    path = tmp_path / "garbled.hwp"
    path.write_bytes(b"fixture")

    extraction, _units = HwpNativeParser().parse(
        path,
        artifact_id="art_fixture",
        document_id="doc_fixture",
        acl_scopes=["workspace:default"],
    )

    # Then the degraded content drags quality well below a clean document's score.
    assert extraction.quality_score < 0.5


def test_native_parser_wraps_corrupted_section_xml_as_typed_parser_error(
    monkeypatch, tmp_path
) -> None:
    # Given a valid .hwpx whose section XML the underlying reader library
    # reports as corrupted while parsing (xml.etree.ElementTree.ParseError
    # is the same sibling-parser exception docx/xlsx/pptx_ooxml already
    # catch, but it was previously missing here).
    class FakeReader:
        def __init__(self, filepath):
            self.filepath = filepath
            self.tables: list[object] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def extract_text(self):
            raise ET.ParseError("no element found: line 1, column 0")

        def get_images(self):
            return []

    monkeypatch.setitem(sys.modules, "hwp_hwpx_parser", SimpleNamespace(Reader=FakeReader))
    path = tmp_path / "corrupted.hwpx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/content.hpf", "<package />")

    # When the native parser reaches the corrupted section XML.
    # Then callers receive the stable parser error instead of a raw ET.ParseError.
    with pytest.raises(ParserError, match="native HWP parse failed"):
        HwpNativeParser().parse(
            path,
            artifact_id="art_corrupted",
            document_id="doc_corrupted",
            acl_scopes=["workspace:default"],
        )


def _minimal_hwpx_section(text: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        '<hp:p id="0" paraPrIDRef="0" styleIDRef="0">'
        f'<hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run>'
        "</hp:p></hs:sec>"
    )


def test_native_parser_extracts_text_from_every_hwpx_section(tmp_path: Path) -> None:
    # Given a real (not mocked) minimal HWPX archive with two sections
    # (section0.xml, section1.xml) - the hwp-hwpx-parser dependency, not a
    # FakeReader, does the actual extraction here.
    path = tmp_path / "multi_section.hwpx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/content.hpf", '<?xml version="1.0"?><package/>')
        archive.writestr("Contents/section0.xml", _minimal_hwpx_section("SECTION_ZERO_MARKER"))
        archive.writestr("Contents/section1.xml", _minimal_hwpx_section("SECTION_ONE_MARKER"))

    # When the native parser extracts text end-to-end through the real library.
    extraction, units = HwpNativeParser().parse(
        path, artifact_id="art_multi", document_id="doc_multi", acl_scopes=["workspace:default"]
    )

    # Then both sections' text are present, not just the first one.
    body = "".join(unit.body for unit in units)
    assert "SECTION_ZERO_MARKER" in body
    assert "SECTION_ONE_MARKER" in body
    assert extraction.status == "succeeded"


def test_native_parser_rejects_encrypted_hwpx_as_typed_parser_error(tmp_path: Path) -> None:
    # Given a real (not mocked) HWPX archive whose manifest declares
    # encryption data - the hwp-hwpx-parser dependency's own extract_text()
    # raises a plain ValueError("Encrypted files are not supported") for
    # this case, which the outer except clause in HwpNativeParser.parse()
    # already covers.
    path = tmp_path / "encrypted.hwpx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/content.hpf", '<?xml version="1.0"?><package/>')
        archive.writestr("Contents/section0.xml", _minimal_hwpx_section("SHOULD_NOT_APPEAR"))
        archive.writestr("META-INF/manifest.xml", "<manifest><encryption-data/></manifest>")

    # When the native parser attempts to extract text through the real library.
    # Then the encrypted-file rejection surfaces as a typed ParserError, not
    # an uncaught ValueError, and no partial/garbled text is emitted.
    with pytest.raises(ParserError, match="native HWP parse failed"):
        HwpNativeParser().parse(
            path, artifact_id="art_encrypted", document_id="doc_encrypted", acl_scopes=["workspace:default"]
        )


def test_registry_places_native_parser_in_the_hwp_chain(tmp_path: Path):
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "parsers": {
                "hwp": {
                    "order": ["hwp-hwpx-parser", "paired_pdf"],
                    "hwp-hwpx-parser": {"enabled": True, "max_chars_per_unit": 123},
                }
            }
        },
    )

    path = tmp_path / "fixture.hwp"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"fixture")
    parser = ParserRegistry.from_settings(settings).find(path)

    assert parser.native is not None
    assert parser.native.max_chars_per_unit == 123
    assert parser.version == "2.0-native-primary"


def test_native_parser_supports_only_matching_hwp_signatures(tmp_path: Path) -> None:
    # Given files whose suffixes and binary signatures either agree or disagree.
    invalid_hwp = tmp_path / "invalid.hwp"
    invalid_hwp.write_text("not an HWP document", encoding="utf-8")
    valid_hwp = tmp_path / "valid.hwp"
    valid_hwp.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"fixture")
    invalid_hwpx = tmp_path / "invalid.hwpx"
    invalid_hwpx.write_text("not an HWPX archive", encoding="utf-8")
    valid_hwpx = tmp_path / "valid.hwpx"
    with zipfile.ZipFile(valid_hwpx, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/content.hpf", "<package />")

    # When support is determined before parser dispatch.
    parser = HwpNativeParser()

    # Then extension-only false positives are rejected while valid signatures remain eligible.
    assert parser.supports(invalid_hwp) is False
    assert parser.supports(valid_hwp) is True
    assert parser.supports(invalid_hwpx) is False
    assert parser.supports(valid_hwpx) is True


def test_container_config_selects_native_hwp_primary(tmp_path: Path) -> None:
    # Given the exact configuration copied into the production container.
    root = Path(__file__).resolve().parents[1]
    settings = Settings.load(root / "config/kip.container.toml")
    path = tmp_path / "fixture.hwp"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"fixture")

    # When the runtime composes the parser chain.
    parser = ParserRegistry.from_settings(settings).find(path)
    kordoc = settings.get("parsers.hwp.kordoc", {}) or {}

    # Then the bundled native parser is primary and Kordoc is explicit opt-in.
    assert parser.native is not None
    assert parser.version == "2.0-native-primary"
    assert kordoc["enabled"] is False
    assert kordoc["argv"][0] == "kordoc"


def test_command_broker_preserves_kordoc_structured_blocks(tmp_path: Path) -> None:
    # Given a Kordoc-compatible payload containing a table, an image, and a typed warning.
    payload = {
        "metadata": {"parserVersion": "4.7.3"},
        "blocks": [
            {
                "type": "paragraph",
                "text": "승인 완료",
                "pageNumber": 1,
                "spans": [{"text": "승인", "bold": True}, {"text": " 완료"}],
                "footnoteText": "결재번호 A-1",
            },
            {
                "type": "table",
                "pageNumber": 2,
                "table": {
                    "rows": 2,
                    "cols": 2,
                    "hasHeader": True,
                    "cells": [
                        [
                            {"text": "항목", "rowSpan": 1, "colSpan": 1, "isHeader": True},
                            {"text": "값", "rowSpan": 1, "colSpan": 1, "isHeader": True},
                        ],
                        [
                            {"text": "일정", "rowSpan": 1, "colSpan": 1},
                            {"text": "2026-08-13", "rowSpan": 1, "colSpan": 1},
                        ],
                    ],
                },
            },
            {
                "type": "image",
                "pageNumber": 2,
                "imageData": {"filename": "도면.png", "mimeType": "image/png"},
            },
        ],
        "warnings": [{"page": 2, "code": "PARTIAL_PARSE", "message": "도형 일부를 건너뜀"}],
    }
    source = tmp_path / "fixture.hwp"
    source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"fixture")
    command = [sys.executable, "-c", f"print({json.dumps(json.dumps(payload))})"]
    broker = HwpParserBroker([CommandParserConfig(name="kordoc", argv=command)])

    # When the command boundary translates the structured payload.
    extraction, units = broker.parse(
        source,
        artifact_id="art_fixture",
        document_id="doc_fixture",
        acl_scopes=["workspace:default"],
    )

    # Then no meaningful block is dropped and machine-readable structure remains available.
    assert [unit.unit_type for unit in units] == ["paragraph", "table", "image"]
    assert units[1].body == "항목\t값\n일정\t2026-08-13"
    assert units[1].metadata["table"]["cells"][1][1]["text"] == "2026-08-13"
    assert units[2].body == "[image: 도면.png]"
    assert units[2].metadata["image"] == {
        "filename": "도면.png",
        "mime_type": "image/png",
    }
    assert extraction.warnings == ["PARTIAL_PARSE page 2: 도형 일부를 건너뜀"]
    # And the locator never trusts kordoc's never-emitted section/sectionNumber
    # keys, and pairs every non-null page with a page_mode so a reader can
    # tell a real page from a section-position approximation (ADR-049).
    assert all(unit.locator.data["section"] is None for unit in units)
    assert [unit.locator.data["page"] for unit in units] == [1, 2, 2]
    assert [unit.locator.data["page_mode"] for unit in units] == [
        "section_approx",
        "section_approx",
        "section_approx",
    ]


def test_command_broker_marks_page_exact_only_when_metadata_says_so(
    tmp_path: Path,
) -> None:
    # Given a payload whose document metadata explicitly claims exact pages.
    payload = {
        "metadata": {"parserVersion": "4.7.3", "pageMode": "exact"},
        "blocks": [{"type": "paragraph", "text": "승인 완료", "pageNumber": 1}],
    }
    source = tmp_path / "fixture.hwp"
    source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"fixture")
    command = [sys.executable, "-c", f"print({json.dumps(json.dumps(payload))})"]
    broker = HwpParserBroker([CommandParserConfig(name="kordoc", argv=command)])

    # When the command boundary translates the payload.
    _extraction, units = broker.parse(
        source,
        artifact_id="art_fixture",
        document_id="doc_fixture",
        acl_scopes=["workspace:default"],
    )

    # Then the locator records the exact page mode instead of the
    # conservative section_approx default.
    assert units[0].locator.data["page"] == 1
    assert units[0].locator.data["page_mode"] == "exact"


def test_command_broker_leaves_page_mode_null_when_no_page_is_placed(
    tmp_path: Path,
) -> None:
    # Given a block that carries no pageNumber at all.
    payload = {
        "metadata": {"parserVersion": "4.7.3"},
        "blocks": [{"type": "paragraph", "text": "승인 완료"}],
    }
    source = tmp_path / "fixture.hwp"
    source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"fixture")
    command = [sys.executable, "-c", f"print({json.dumps(json.dumps(payload))})"]
    broker = HwpParserBroker([CommandParserConfig(name="kordoc", argv=command)])

    # When the command boundary translates the payload.
    _extraction, units = broker.parse(
        source,
        artifact_id="art_fixture",
        document_id="doc_fixture",
        acl_scopes=["workspace:default"],
    )

    # Then page_mode is never fabricated for an absent page.
    assert units[0].locator.data["page"] is None
    assert units[0].locator.data["page_mode"] is None


def _hwpx_section_xml(text: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        '<hp:p id="0" paraPrIDRef="0" styleIDRef="0">'
        f'<hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run>'
        "</hp:p></hs:sec>"
    )


def test_native_parser_labels_sections_numerically_despite_lexical_file_order(
    tmp_path: Path,
) -> None:
    # Given a real (not mocked) HWPX archive with 11 sections, so section10
    # sorts lexically before section2 in the dependency's own
    # _get_section_files() (a real ordering quirk this feature does not
    # attempt to fix - see hwp_native._reconstruct_section_spans).
    path = tmp_path / "eleven_sections.hwpx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/content.hpf", '<?xml version="1.0"?><package/>')
        for index in range(11):
            archive.writestr(f"Contents/section{index}.xml", _hwpx_section_xml(f"MARKER_{index}_END"))

    # When the section reconstruction runs through the real hwp-hwpx-parser
    # dependency directly (bypassing chunk splitting, whose windows can
    # straddle multiple small sections and would make a body-substring
    # assertion ambiguous).
    from kip.adapters.parsers import hwp_native as hwp_native_module

    Reader = import_module("hwp_hwpx_parser").Reader
    with Reader(path) as reader:
        text = reader.extract_text()
        spans = hwp_native_module._reconstruct_section_spans(reader, path, text)

    # Then reconstruction verified against extract_text() (spans is not
    # None) and each section's true numeric index - parsed from its
    # filename, never its position among the lexically-sorted files -
    # matches the marker embedded in that section's own text.
    assert spans is not None
    assert {span.section for span in spans} == set(range(11))
    for span in spans:
        assert f"MARKER_{span.section}_END" in text[span.start : span.end]
        assert f"MARKER_{span.section}_END" not in text[: span.start]
        assert f"MARKER_{span.section}_END" not in text[span.end :]


class _FakeHwp5Backend:
    """Duck-types just the private HWP5Reader surface
    _reconstruct_section_spans reaches into (_reset_counters,
    _iter_sections, _read_section, _extract_section_text), so the HWP5
    branch can be exercised without a real OLE-compound-file fixture."""

    def __init__(self, sections: dict[int, str]):
        self._sections = sections
        self.reset_calls = 0

    def _reset_counters(self):
        self.reset_calls += 1

    def _iter_sections(self):
        return iter(sorted(self._sections))

    def _read_section(self, section_idx):
        return section_idx

    def _extract_section_text(self, section_data, options):
        return self._sections[section_data]


class _FakeHwp5Reader:
    def __init__(self, backend: _FakeHwp5Backend):
        self._backend = backend

    def _get_reader(self):
        return self._backend


def test_reconstruct_section_spans_succeeds_for_hwp5_numeric_stream_order(
    tmp_path: Path,
) -> None:
    # Given a fake HWP5 backend whose _iter_sections() already yields the
    # real BodyText/SectionN order (0, 1, 2 - HWP5 has no lexical-sort bug,
    # unlike HWPX's _get_section_files()).
    from kip.adapters.parsers import hwp_native as hwp_native_module

    sections = {0: "ZERO", 1: "ONE", 2: "TWO"}
    backend = _FakeHwp5Backend(sections)
    reader = _FakeHwp5Reader(backend)
    full_text = "\n\n".join(sections[i] for i in sorted(sections))
    path = tmp_path / "fixture.hwp"

    # When the reconstruction runs.
    spans = hwp_native_module._reconstruct_section_spans(reader, path, full_text)

    # Then it verifies and labels every section with its real numeric index,
    # having reset counters first (mirroring HWP5Reader.extract_text()'s own
    # preamble) so re-numbered footnote/endnote markers would stay correct.
    assert backend.reset_calls == 1
    assert spans is not None
    assert [(span.section, full_text[span.start : span.end]) for span in spans] == [
        (0, "ZERO"),
        (1, "ONE"),
        (2, "TWO"),
    ]


def test_reconstruct_section_spans_falls_back_to_none_on_mismatch(
    tmp_path: Path,
) -> None:
    # Given a fake HWP5 backend whose per-section reconstruction does NOT
    # concatenate to the text the caller already extracted (simulating
    # dependency drift or an unexpected internal structure).
    from kip.adapters.parsers import hwp_native as hwp_native_module

    backend = _FakeHwp5Backend({0: "ZERO", 1: "ONE"})
    reader = _FakeHwp5Reader(backend)
    path = tmp_path / "fixture.hwp"

    # When the reconstruction is checked against text that cannot match.
    spans = hwp_native_module._reconstruct_section_spans(
        reader, path, "SOMETHING ENTIRELY DIFFERENT"
    )

    # Then it fails safe: no spans, so callers fall back to section: None
    # and a warning instead of ever emitting a wrong section number.
    assert spans is None


def test_reconstruct_section_spans_returns_none_for_unsupported_suffix(
    tmp_path: Path,
) -> None:
    # Given a path whose suffix is neither .hwp nor .hwpx.
    from kip.adapters.parsers import hwp_native as hwp_native_module

    backend = _FakeHwp5Backend({0: "ZERO"})
    reader = _FakeHwp5Reader(backend)
    path = tmp_path / "fixture.txt"

    # When reconstruction is attempted.
    spans = hwp_native_module._reconstruct_section_spans(reader, path, "ZERO")

    # Then it declines rather than guessing.
    assert spans is None


def test_section_for_offset_attributes_gaps_and_out_of_range_offsets() -> None:
    # Given section spans with a gap between them (the paragraph_separator).
    from kip.adapters.parsers.hwp_native import _section_for_offset, _SectionSpan

    spans = [_SectionSpan(section=0, start=0, end=4), _SectionSpan(section=1, start=6, end=10)]

    # When offsets land inside a span, inside the gap, and past the end.
    # Then interior offsets resolve to their own section, an offset inside
    # the gap attributes forward to the next section, and an offset past
    # every span falls back to the last section rather than raising.
    assert _section_for_offset(spans, 2) == 0
    assert _section_for_offset(spans, 5) == 1
    assert _section_for_offset(spans, 999) == 1
    assert _section_for_offset([], 0) is None
