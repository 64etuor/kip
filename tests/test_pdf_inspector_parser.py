from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from kip.adapters.parsers import pdf_inspector_runtime
from kip.adapters.parsers.pdf_inspector import PdfInspectorParser
from kip.adapters.parsers.registry import ParserRegistry
from kip.errors import ConfigurationError, DependencyUnavailableError, ParserError
from kip.ports.ocr import OcrBlock, OcrDocument
from kip.settings import Settings


class FixtureOcr:
    name = "fixture-korean-ocr"
    version = "1"

    def recognize(self, paths: tuple[Path, ...]) -> tuple[OcrDocument, ...]:
        return (
            OcrDocument(
                source_path=paths[0],
                blocks=(
                    OcrBlock(
                        text="스캔 페이지 근거",
                        block_type="paragraph",
                        page=2,
                        bbox={"x": 10, "y": 20, "width": 200, "height": 30},
                        metadata={},
                    ),
                ),
                metadata={},
                warnings=(),
            ),
        )


def _save_pdf(path: Path, draw_pages) -> None:
    document = pymupdf.open()
    draw_pages(document)
    document.save(path)
    document.close()


def test_pdf_inspector_emits_page_markdown_with_stable_locator(tmp_path: Path) -> None:
    # Given a native-text PDF page.
    path = tmp_path / "page.pdf"
    _save_pdf(
        path,
        lambda document: document.new_page().insert_text(
            (72, 72), "Searchable evidence text with enough characters"
        ),
    )

    # When the pdf-inspector backend parses it.
    extraction, units = PdfInspectorParser().parse(
        path,
        artifact_id="art_page",
        document_id="doc_page",
        acl_scopes=["workspace:default"],
    )

    # Then the page remains exact evidence and records its backend.
    assert [unit.unit_type for unit in units] == ["pdf_page"]
    assert "Searchable evidence" in units[0].body
    assert units[0].locator.type == "pdf_page"
    assert units[0].locator.data == {"page": 1}
    assert units[0].metadata["source"] == "pdf_inspector"
    assert extraction.parser_name == "pdf-inspector"
    assert extraction.parser_version == "1.14.2"
    assert extraction.status == "succeeded"


def test_pdf_inspector_routes_unreadable_page_to_existing_ocr(tmp_path: Path) -> None:
    # Given one native-text page followed by one blank image-style page.
    path = tmp_path / "mixed.pdf"

    def draw(document: pymupdf.Document) -> None:
        document.new_page().insert_text(
            (72, 72), "Searchable evidence text with enough characters"
        )
        document.new_page()

    _save_pdf(path, draw)

    # When the hybrid backend runs with the shared OCR adapter.
    extraction, units = PdfInspectorParser(ocr=FixtureOcr()).parse(
        path,
        artifact_id="art_mixed",
        document_id="doc_mixed",
        acl_scopes=["workspace:default"],
    )

    # Then native pages remain and OCR is admitted only for page two.
    assert [unit.unit_type for unit in units] == ["pdf_page", "pdf_page", "pdf_ocr"]
    assert units[-1].body == "스캔 페이지 근거"
    assert units[-1].locator.data["page"] == 2
    assert extraction.metadata["ocr_candidate_reasons"] == {"2": "low_text"}
    assert extraction.metadata["ocr_page_count"] == 1
    assert extraction.status == "succeeded"


def test_pdf_inspector_promotes_borderless_markdown_table(tmp_path: Path) -> None:
    # Given an aligned borderless table that PyMuPDF lines_strict cannot see.
    path = tmp_path / "borderless.pdf"

    def draw(document: pymupdf.Document) -> None:
        page = document.new_page(width=500, height=400)
        for x, y, text in (
            (60, 80, "Item"),
            (260, 80, "Amount"),
            (60, 110, "Labor"),
            (260, 110, "1200"),
            (60, 140, "Equipment"),
            (260, 140, "800"),
        ):
            page.insert_text((x, y), text)

    _save_pdf(path, draw)

    # When the inspector emits a Markdown table.
    _extraction, units = PdfInspectorParser().parse(
        path,
        artifact_id="art_borderless",
        document_id="doc_borderless",
        acl_scopes=["workspace:default"],
    )

    # Then it becomes additive structured table evidence.
    table = next(unit for unit in units if unit.unit_type == "pdf_table")
    assert "1200" in table.body
    assert "800" in table.body
    assert table.locator.data == {"page": 1, "end_page": 1, "table_index": 0}
    assert table.metadata["source"] == "pdf_inspector"
    assert table.metadata["strategy"] == "markdown"


def test_pdf_inspector_falls_back_for_detected_bordered_table(tmp_path: Path) -> None:
    # Given a bordered 2x2 table the inspector detects but does not render as a table.
    path = tmp_path / "bordered.pdf"

    def draw(document: pymupdf.Document) -> None:
        page = document.new_page(width=400, height=300)
        for x in (50, 200, 350):
            page.draw_line((x, 50), (x, 150))
        for y in (50, 100, 150):
            page.draw_line((50, y), (350, y))
        for x, y, text in (
            (60, 80, "A1"),
            (210, 80, "B1"),
            (60, 130, "A2"),
            (210, 130, "B2"),
        ):
            page.insert_text((x, y), text)

    _save_pdf(path, draw)

    # When the hybrid parser handles the detected table page.
    _extraction, units = PdfInspectorParser().parse(
        path,
        artifact_id="art_bordered",
        document_id="doc_bordered",
        acl_scopes=["workspace:default"],
    )

    # Then PyMuPDF preserves the existing exact-table body and source metadata.
    table = next(unit for unit in units if unit.unit_type == "pdf_table")
    assert table.body == "|A1|B1|\n|---|---|\n|A2|B2|\n\n"
    assert table.metadata["source"] == "pymupdf.find_tables"
    assert _extraction.metadata["pymupdf_fallback_page_count"] == 1


def test_pdf_inspector_does_not_promote_decorative_box(tmp_path: Path) -> None:
    # Given a single-row decorative contact box and ordinary prose.
    path = tmp_path / "decorative.pdf"

    def draw(document: pymupdf.Document) -> None:
        page = document.new_page(width=400, height=300)
        page.draw_rect((50, 50, 350, 90))
        page.draw_line((200, 50), (200, 90))
        page.insert_text((60, 75), "Contact")
        page.insert_text((210, 75), "02-000-0000")
        page.insert_text((50, 150), "Ordinary prose outside a decorative box.")

    _save_pdf(path, draw)

    # When it is parsed by the hybrid backend.
    extraction, units = PdfInspectorParser().parse(
        path,
        artifact_id="art_decorative",
        document_id="doc_decorative",
        acl_scopes=["workspace:default"],
    )

    # Then no table fact is invented.
    assert [unit.unit_type for unit in units] == ["pdf_page"]
    assert extraction.status == "succeeded"


def test_pdf_inspector_wraps_malformed_pdf_as_parser_error(tmp_path: Path) -> None:
    # Given non-PDF bytes with a PDF extension.
    path = tmp_path / "invalid.pdf"
    path.write_bytes(b"not a pdf")

    # When parsing crosses the native extension boundary.
    # Then the failure is typed and contains no native traceback contract.
    with pytest.raises(ParserError, match="PDF Inspector parse failed"):
        PdfInspectorParser().parse(
            path,
            artifact_id="art_invalid",
            document_id="doc_invalid",
            acl_scopes=["workspace:default"],
        )


def test_pdf_inspector_rejects_unreviewed_native_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given an installed native wheel whose version differs from the reviewed pin.
    path = tmp_path / "version.pdf"
    _save_pdf(path, lambda document: document.new_page())
    monkeypatch.setattr(pdf_inspector_runtime, "version", lambda _name: "1.15.0")

    # When parsing checks the runtime package identity.
    # Then unreviewed parser drift is rejected before source extraction.
    with pytest.raises(DependencyUnavailableError, match=r"expected 1\.14\.2"):
        PdfInspectorParser().parse(
            path,
            artifact_id="art_version",
            document_id="doc_version",
            acl_scopes=["workspace:default"],
        )


def test_registry_rejects_unknown_pdf_backend(tmp_path: Path) -> None:
    # Given an unsupported PDF backend name.
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={"parsers": {"pdf": {"backend": "unknown"}}},
    )

    # When the parser registry composes adapters.
    # Then configuration fails instead of silently selecting another parser.
    with pytest.raises(ConfigurationError, match="unsupported PDF parser backend"):
        ParserRegistry.from_settings(settings)
