from pathlib import Path

import fitz
import pytest

from kip.adapters.parsers import pdf as pdf_module
from kip.adapters.parsers.pdf import PdfParser
from kip.errors import ParserError
from kip.ports.ocr import OcrBlock, OcrDocument


class KoreanOcr:
    name = "fixture-korean-ocr"
    version = "1"

    def recognize(self, paths: tuple[Path, ...]) -> tuple[OcrDocument, ...]:
        return (
            OcrDocument(
                source_path=paths[0],
                blocks=(
                    OcrBlock(
                        text="스캔된 품질 개선 보고서",
                        block_type="paragraph",
                        page=2,
                        bbox={"x": 12, "y": 30, "width": 240, "height": 36},
                        metadata={"confidence": 0.97},
                    ),
                ),
                metadata={"ocrEngine": "PP-OCRv5 korean"},
                warnings=(),
            ),
        )


class FailingOcr:
    name = "fixture-failing-ocr"
    version = "1"

    def recognize(self, paths: tuple[Path, ...]) -> tuple[OcrDocument, ...]:
        raise ParserError("fixture OCR unavailable")


def test_pdf_quality_reflects_low_text_page_coverage(tmp_path: Path) -> None:
    # Given: a PDF with one searchable page and one image-only/blank page.
    path = tmp_path / "mixed.pdf"
    document = fitz.open()
    document.new_page().insert_text((72, 72), "searchable evidence text with enough characters")
    document.new_page()
    document.save(path)
    document.close()

    # When: the PDF is parsed through the production adapter.
    extraction, units = PdfParser().parse(
        path,
        artifact_id="art_pdf",
        document_id="doc_pdf",
        acl_scopes=["workspace:default"],
    )

    # Then: partial visual coverage lowers quality and is machine-readable.
    assert extraction.status == "partial"
    assert extraction.quality_score == 0.475
    assert extraction.metadata == {
        "page_count": 2,
        "low_text_page_count": 1,
        "ocr_candidate_page_count": 1,
        "ocr_candidate_reasons": {"2": "low_text"},
        "text_coverage": 0.5,
    }
    assert len(units) == 2


def test_pdf_adds_korean_ocr_unit_when_page_has_no_text(tmp_path: Path) -> None:
    # Given a PDF whose second page has no searchable text and a Korean OCR adapter.
    path = tmp_path / "scanned.pdf"
    document = fitz.open()
    document.new_page().insert_text(
        (72, 72), "searchable evidence text with enough characters"
    )
    document.new_page()
    document.save(path)
    document.close()

    # When the PDF is parsed as an OCR-enriched candidate extraction.
    extraction, units = PdfParser(ocr=KoreanOcr()).parse(
        path,
        artifact_id="art_pdf_ocr",
        document_id="doc_pdf_ocr",
        acl_scopes=["workspace:default"],
    )

    # Then native pages remain and Korean OCR gets a page/bbox evidence unit.
    assert [unit.unit_type for unit in units] == ["pdf_page", "pdf_page", "pdf_ocr"]
    assert units[-1].body == "스캔된 품질 개선 보고서"
    assert units[-1].locator.type == "pdf_ocr"
    assert units[-1].locator.data == {
        "page": 2,
        "bbox": {"x": 12, "y": 30, "width": 240, "height": 36},
    }
    assert extraction.parser_name == "pymupdf+fixture-korean-ocr"
    assert extraction.status == "succeeded"
    assert extraction.metadata["ocr_block_count"] == 1


def test_pdf_flags_private_use_glyphs_for_korean_ocr() -> None:
    # Given a non-empty PDF text layer dominated by private-use glyph mappings.
    broken_text = "\ue000\ue001\ue002\ue003\ue004" * 10

    # When page text quality is classified.
    reason = pdf_module._ocr_reason(broken_text)

    # Then the page is routed to OCR despite exceeding the low-text threshold.
    assert reason == "high_pua"


def test_pdf_preserves_native_pages_when_ocr_fails(tmp_path: Path) -> None:
    # Given a searchable page plus an OCR candidate and a failing OCR command.
    path = tmp_path / "mixed.pdf"
    document = fitz.open()
    document.new_page().insert_text(
        (72, 72), "searchable evidence text with enough characters"
    )
    document.new_page()
    document.save(path)
    document.close()

    # When optional OCR enrichment fails.
    extraction, units = PdfParser(ocr=FailingOcr()).parse(
        path,
        artifact_id="art_pdf_fallback",
        document_id="doc_pdf_fallback",
        acl_scopes=["workspace:default"],
    )

    # Then native page evidence remains and the failure is explicit.
    assert [unit.unit_type for unit in units] == ["pdf_page", "pdf_page"]
    assert extraction.status == "partial"
    assert extraction.warnings[-1] == "OCR_FAILED: fixture OCR unavailable"


def test_pdf_raises_typed_error_for_non_pdf_content_with_pdf_extension(
    tmp_path: Path,
) -> None:
    # Given a file whose content is a PNG image but whose extension is .pdf
    # (pymupdf 1.28.0 raises its own pymupdf.mupdf.FzErrorFormat - not a
    # stdlib OSError/RuntimeError/ValueError - once it discovers the page
    # content does not match the format it inferred from the file).
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 200)

    # When the parser attempts to extract page text.
    # Then it fails cleanly as a typed ParserError instead of an uncaught crash.
    with pytest.raises(ParserError, match="PDF parse failed"):
        PdfParser().parse(
            path,
            artifact_id="art_fake_pdf",
            document_id="doc_fake_pdf",
            acl_scopes=["workspace:default"],
        )


def test_pdf_raises_typed_error_for_encrypted_pdf_with_tables_enabled(
    tmp_path: Path,
) -> None:
    # Given an AES-256-encrypted PDF (needs a password to open).
    path = tmp_path / "encrypted.pdf"
    document = fitz.open()
    document.new_page().insert_text((72, 72), "secret content with enough characters here")
    document.save(
        path,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        owner_pw="owner",
    )
    document.close()

    # When parsed with table detection on (the default).
    # Then behavior is unchanged from today: a typed ParserError, not a
    # partial/garbled extraction and not a table-detection-specific crash.
    with pytest.raises(ParserError, match="PDF parse failed"):
        PdfParser().parse(
            path,
            artifact_id="art_encrypted_pdf",
            document_id="doc_encrypted_pdf",
            acl_scopes=["workspace:default"],
        )


_FILLER_TEXT = "This is filler prose kept outside the table box so the page clears the low-text OCR threshold on its own."


def _bordered_two_by_two_table_page(page: fitz.Page) -> None:
    x0, y0, x1, y1 = 50, 50, 350, 150
    page.draw_rect((x0, y0, x1, y1))
    page.draw_line((x0, 100), (x1, 100))
    page.draw_line((200, y0), (200, y1))
    page.insert_text((60, 80), "A1")
    page.insert_text((210, 80), "B1")
    page.insert_text((60, 130), "A2")
    page.insert_text((210, 130), "B2")
    page.insert_text((60, 250), _FILLER_TEXT)


def _decorative_single_row_box_page(page: fitz.Page) -> None:
    # A single bordered row with an internal vertical divider - the exact
    # "1x1 footer/callout box" shape measured as the real-corpus false
    # positive this row_count/col_count filter must suppress (ADR-049).
    x0, y0, x1, y1 = 50, 300, 350, 340
    page.draw_rect((x0, y0, x1, y1))
    page.draw_line((200, y0), (200, y1))
    page.insert_text((60, 325), "Contact")
    page.insert_text((210, 325), "02-000-0000")
    page.insert_text((60, 200), _FILLER_TEXT)


def test_pdf_bordered_table_becomes_a_pdf_table_unit_with_correct_cells(
    tmp_path: Path,
) -> None:
    # Given a page with one bordered 2x2 table.
    path = tmp_path / "table.pdf"
    document = fitz.open()
    _bordered_two_by_two_table_page(document.new_page(width=400, height=400))
    document.save(path)
    document.close()

    # When parsed with table detection on.
    extraction, units = PdfParser().parse(
        path,
        artifact_id="art_table",
        document_id="doc_table",
        acl_scopes=["workspace:default"],
    )

    # Then an additive pdf_table unit carries the correct cells, locator, and
    # metadata shape, alongside the unchanged pdf_page unit.
    assert [unit.unit_type for unit in units] == ["pdf_page", "pdf_table"]
    table_unit = units[1]
    assert table_unit.ordinal == 1
    assert table_unit.body == "|A1|B1|\n|---|---|\n|A2|B2|\n\n"
    assert table_unit.locator.type == "pdf_table"
    assert table_unit.locator.data == {"page": 1, "end_page": 1, "table_index": 0}
    assert table_unit.metadata["row_count"] == 2
    assert table_unit.metadata["col_count"] == 2
    assert table_unit.metadata["strategy"] == "lines_strict"
    assert table_unit.metadata["source"] == "pymupdf.find_tables"
    assert table_unit.metadata["bbox"] == [50.0, 50.0, 350.0, 150.0]
    assert extraction.status == "succeeded"
    assert extraction.warnings == []


def test_pdf_decorative_single_row_box_is_not_promoted_to_a_table_unit(
    tmp_path: Path,
) -> None:
    # Given a page containing only a decorative single-row bordered box
    # (e.g. a footer callout) with no other prose.
    path = tmp_path / "decorative.pdf"
    document = fitz.open()
    _decorative_single_row_box_page(document.new_page(width=400, height=400))
    document.save(path)
    document.close()

    # When parsed with table detection on.
    extraction, units = PdfParser().parse(
        path,
        artifact_id="art_decorative",
        document_id="doc_decorative",
        acl_scopes=["workspace:default"],
    )

    # Then the row_count>=2 and col_count>=2 filter suppresses it: only the
    # page unit is emitted, never a pdf_table unit for a 1-row box.
    assert [unit.unit_type for unit in units] == ["pdf_page"]
    assert extraction.status == "succeeded"


def test_pdf_prose_only_page_emits_no_table_units(tmp_path: Path) -> None:
    # Given a page with only flowing prose text and no lines/borders at all.
    path = tmp_path / "prose.pdf"
    document = fitz.open()
    document.new_page().insert_text(
        (72, 72), "이것은 표가 전혀 없는 일반적인 문단입니다. 충분한 글자 수를 포함합니다."
    )
    document.save(path)
    document.close()

    # When parsed with table detection on.
    extraction, units = PdfParser().parse(
        path,
        artifact_id="art_prose",
        document_id="doc_prose",
        acl_scopes=["workspace:default"],
    )

    # Then no pdf_table unit is hallucinated from plain prose.
    assert [unit.unit_type for unit in units] == ["pdf_page"]
    assert extraction.status == "succeeded"


def test_pdf_table_detection_never_changes_pdf_page_units(tmp_path: Path) -> None:
    # Given a page with a bordered table plus prose, parsed twice: once with
    # table detection on and once with it off.
    path = tmp_path / "mixed_table.pdf"
    document = fitz.open()
    page = document.new_page(width=400, height=400)
    _bordered_two_by_two_table_page(page)
    page.insert_text((60, 250), "표 아래의 일반 문단입니다.")
    document.save(path)
    document.close()

    extraction_on, units_on = PdfParser(tables_enabled=True).parse(
        path, artifact_id="art_on", document_id="doc_on", acl_scopes=["workspace:default"]
    )
    extraction_off, units_off = PdfParser(tables_enabled=False).parse(
        path, artifact_id="art_off", document_id="doc_off", acl_scopes=["workspace:default"]
    )

    # Then pdf_page units are byte-identical whether or not table extraction
    # ran, and only the "on" run emits the additive pdf_table unit.
    page_units_on = [unit for unit in units_on if unit.unit_type == "pdf_page"]
    page_units_off = [unit for unit in units_off if unit.unit_type == "pdf_page"]
    assert [unit.body for unit in page_units_on] == [unit.body for unit in page_units_off]
    assert [unit.locator.data for unit in page_units_on] == [
        unit.locator.data for unit in page_units_off
    ]
    assert [unit.unit_type for unit in units_on] == ["pdf_page", "pdf_table"]
    assert [unit.unit_type for unit in units_off] == ["pdf_page"]
    assert extraction_on.metadata["page_count"] == extraction_off.metadata["page_count"] == 1


def test_pdf_table_render_is_deterministic_across_repeated_parses(tmp_path: Path) -> None:
    # Given the same bordered-table PDF parsed twice independently.
    path = tmp_path / "table_repeat.pdf"
    document = fitz.open()
    _bordered_two_by_two_table_page(document.new_page(width=400, height=400))
    document.save(path)
    document.close()

    _extraction_a, units_a = PdfParser().parse(
        path, artifact_id="art_a", document_id="doc_a", acl_scopes=["workspace:default"]
    )
    _extraction_b, units_b = PdfParser().parse(
        path, artifact_id="art_b", document_id="doc_b", acl_scopes=["workspace:default"]
    )

    # Then the rendered table body is byte-for-byte identical (same input ->
    # same bytes), as required for a stable, citable evidence unit.
    table_body_a = next(unit.body for unit in units_a if unit.unit_type == "pdf_table")
    table_body_b = next(unit.body for unit in units_b if unit.unit_type == "pdf_table")
    assert table_body_a == table_body_b


def test_pdf_table_detection_failure_degrades_to_a_warning_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a normal PDF and a find_tables() that raises unexpectedly.
    path = tmp_path / "normal.pdf"
    document = fitz.open()
    document.new_page().insert_text(
        (72, 72), "searchable evidence text with enough characters"
    )
    document.save(path)
    document.close()

    import pymupdf

    def _boom(self: object, strategy: str) -> object:
        raise RuntimeError("synthetic table-detection failure")

    monkeypatch.setattr(pymupdf.Page, "find_tables", _boom)

    # When the PDF is parsed.
    extraction, units = PdfParser().parse(
        path,
        artifact_id="art_boom",
        document_id="doc_boom",
        acl_scopes=["workspace:default"],
    )

    # Then the page still emits its text unit and the failure degrades to a
    # typed warning instead of failing the page or the document.
    assert [unit.unit_type for unit in units] == ["pdf_page"]
    assert extraction.status == "partial"
    assert any(warning.startswith("TABLE_DETECTION_FAILED:") for warning in extraction.warnings)


def test_pdf_tables_disabled_emits_no_pdf_table_units(tmp_path: Path) -> None:
    # Given a page with a bordered table.
    path = tmp_path / "table_off.pdf"
    document = fitz.open()
    _bordered_two_by_two_table_page(document.new_page(width=400, height=400))
    document.save(path)
    document.close()

    # When parsed with the feature explicitly disabled.
    extraction, units = PdfParser(tables_enabled=False).parse(
        path,
        artifact_id="art_off_only",
        document_id="doc_off_only",
        acl_scopes=["workspace:default"],
    )

    # Then no pdf_table unit is emitted at all.
    assert [unit.unit_type for unit in units] == ["pdf_page"]
    assert extraction.status == "succeeded"


def test_registry_wires_pdf_tables_enabled_from_settings(tmp_path: Path) -> None:
    from kip.adapters.parsers.registry import ParserRegistry
    from kip.settings import Settings

    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={"parsers": {"pdf": {"tables_enabled": False}}},
    )
    path = tmp_path / "fixture.pdf"
    path.write_bytes(b"%PDF-1.4\n%fake")

    parser = ParserRegistry.from_settings(settings).find(path)

    assert parser._tables_enabled is False
