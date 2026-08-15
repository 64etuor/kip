from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

from kip.adapters.parsers.hwp_broker import CommandParserConfig, HwpParserBroker
from kip.adapters.parsers.hwp_native import HwpNativeParser, split_text
from kip.adapters.parsers.registry import ParserRegistry
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

    assert extraction.parser_name == "hwp-hwpx-parser"
    assert extraction.status == "succeeded"
    assert extraction.metadata["table_count"] == 1
    assert extraction.metadata["image_count"] == 1
    assert len(units) > 1
    assert all(len(unit.body) <= 40 for unit in units)
    assert all(unit.locator.type == "hwp_structure" for unit in units)
    assert all("section" in unit.locator.data for unit in units)


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
    # Given a long, clean, fully-Hangul extraction with no warnings.
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
    # matching the flat 0.95 the parser used before content-derived scoring.
    assert extraction.status == "succeeded"
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
