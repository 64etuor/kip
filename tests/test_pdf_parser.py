from pathlib import Path

import fitz

from kip.adapters.parsers.pdf import PdfParser


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
        "text_coverage": 0.5,
    }
    assert len(units) == 2
