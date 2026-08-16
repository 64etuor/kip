from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from kip.adapters.parsers.hwp_native import HwpNativeParser, split_text
from kip.adapters.parsers.registry import ParserRegistry, raw_parser_by_key
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
            self.tables: list[str] = []

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
            self.tables: list[str] = []

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
    isolated = ParserRegistry.from_settings(settings).find(path)
    parser = raw_parser_by_key(settings, "hwp")
    kordoc = settings.get("parsers.hwp.kordoc", {}) or {}

    # Then the bundled native parser is primary and Kordoc is explicit opt-in.
    assert parser.native is not None
    assert parser.version == "2.0-native-primary"
    assert isolated.name == parser.name
    assert isolated.version == parser.version
    assert kordoc["enabled"] is False
    assert kordoc["argv"][0] == "kordoc"
