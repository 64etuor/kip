from pathlib import Path

import fitz

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
