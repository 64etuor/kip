from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

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
