from __future__ import annotations

from pathlib import Path
from typing import Literal

from kip.adapters.parsers.pdf_inspector_tables import extract_markdown_table_units
from kip.adapters.parsers.pdf_ocr import (
    PdfOcrContext,
    page_was_ocrd,
    pdf_ocr_reason,
    pdf_ocr_units,
)
from kip.adapters.parsers.pdf_tables import (
    PdfTableContext,
    extract_pymupdf_fallback_tables,
)
from kip.domain.json_types import JsonObject, JsonValue
from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.domain.text import normalize_text, strip_inline_markup
from kip.errors import DependencyUnavailableError, ParserError
from kip.ids import new_id, sha256_bytes, stable_id
from kip.ports.ocr import OcrPort


class PdfInspectorParser:
    name = "pdf-inspector"
    version = "1.19.0"

    def __init__(self, ocr: OcrPort | None = None) -> None:
        self._ocr = ocr

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def parse(
        self,
        path: Path,
        *,
        artifact_id: str,
        document_id: str,
        acl_scopes: list[str],
    ) -> tuple[ExtractionRun, list[ContentUnit]]:
        try:
            from kip.adapters.parsers.pdf_inspector_runtime import (
                extract_inspector_document,
            )
        except ImportError as error:
            raise DependencyUnavailableError(
                "Install the extractors extra for pdf-inspector parsing"
            ) from error
        try:
            inspected = extract_inspector_document(path)
        except DependencyUnavailableError:
            raise
        except ValueError as error:
            raise ParserError(f"PDF Inspector parse failed: {path}: {error}") from error
        if not inspected.pages:
            raise ParserError(f"PDF Inspector parse failed: {path}: no pages")

        extraction_id = new_id("ext")
        units: list[ContentUnit] = []
        warnings: list[str] = []
        ocr_candidates: dict[int, str] = {}
        inspector_ocr_pages: set[int] = set()
        for page in inspected.pages:
            page_number = page.index + 1
            reason = (
                page.ocr_reason or pdf_ocr_reason(page.markdown)
                if page.needs_ocr
                else pdf_ocr_reason(page.markdown)
            )
            if reason is not None:
                ocr_candidates[page_number] = reason
                warnings.append(f"page {page_number}: OCR candidate ({reason})")
            if page.needs_ocr:
                inspector_ocr_pages.add(page_number)
            # `body` (read, snippets) keeps the extractor's Markdown as exact
            # evidence; lexical search and embeddings use it without inline markup.
            normalized = normalize_text(strip_inline_markup(page.markdown))
            units.append(
                ContentUnit(
                    id=stable_id("unit", extraction_id, str(page.index)),
                    extraction_id=extraction_id,
                    document_id=document_id,
                    artifact_id=artifact_id,
                    ordinal=page.index,
                    unit_type="pdf_page",
                    title=f"{path.name} - page {page_number}",
                    body=page.markdown,
                    body_normalized=normalized,
                    lexical_text=normalized,
                    locator=EvidenceLocator(type="pdf_page", data={"page": page_number}),
                    acl_scopes=acl_scopes,
                    metadata={
                        "page": page_number,
                        "source": "pdf_inspector",
                        "needs_ocr": page.needs_ocr,
                    },
                )
            )

        table_pages: set[int] = set()
        next_ordinal = len(units)
        for page in inspected.pages:
            result = extract_markdown_table_units(
                page.markdown,
                page.index,
                PdfTableContext(
                    extraction_id=extraction_id,
                    artifact_id=artifact_id,
                    document_id=document_id,
                    acl_scopes=tuple(acl_scopes),
                    start_ordinal=next_ordinal,
                ),
            )
            units.extend(result.units)
            next_ordinal = result.next_ordinal
            if result.units:
                table_pages.add(page.index + 1)

        fallback_pages = tuple(
            page_number - 1
            for page_number in sorted(inspected.pages_with_tables - table_pages)
            if page_number not in inspector_ocr_pages
        )
        fallback = extract_pymupdf_fallback_tables(
            path,
            fallback_pages,
            PdfTableContext(
                extraction_id=extraction_id,
                artifact_id=artifact_id,
                document_id=document_id,
                acl_scopes=tuple(acl_scopes),
                start_ordinal=next_ordinal,
            ),
        )
        units.extend(fallback.units)
        warnings.extend(fallback.warnings)

        page_count = len(inspected.pages)
        candidate_count = len(ocr_candidates)
        text_coverage = (page_count - candidate_count) / page_count
        final_text_coverage = text_coverage
        table_pages_json: list[JsonValue] = []
        table_pages_json.extend(sorted(inspected.pages_with_tables))
        column_pages_json: list[JsonValue] = []
        column_pages_json.extend(sorted(inspected.pages_with_columns))
        metadata: JsonObject = {
            "page_count": page_count,
            "low_text_page_count": sum(
                reason == "low_text" for reason in ocr_candidates.values()
            ),
            "ocr_candidate_page_count": candidate_count,
            "ocr_candidate_reasons": {
                str(page): reason for page, reason in ocr_candidates.items()
            },
            "text_coverage": text_coverage,
            "pdf_backend": "pdf_inspector",
            "pages_with_tables": table_pages_json,
            "pages_with_columns": column_pages_json,
            "complex_layout": inspected.is_complex,
            "markdown_table_page_count": len(table_pages),
            "pymupdf_fallback_page_count": len(fallback_pages),
        }
        parser_name = self.name
        if self._ocr is not None and ocr_candidates:
            parser_name = f"{self.name}+{self._ocr.name}"
            try:
                documents = self._ocr.recognize((path,))
            except ParserError as error:
                warnings.append(f"OCR_FAILED: {error}")
            else:
                ocr_units = pdf_ocr_units(
                    documents,
                    PdfOcrContext(
                        extraction_id=extraction_id,
                        artifact_id=artifact_id,
                        document_id=document_id,
                        acl_scopes=tuple(acl_scopes),
                        ordinal_start=len(units),
                        candidate_pages=frozenset(ocr_candidates),
                    ),
                )
                units.extend(ocr_units)
                covered_pages = {
                    unit.locator.data.get("page")
                    for unit in ocr_units
                    if isinstance(unit.locator.data.get("page"), int)
                }
                remaining = max(0, candidate_count - len(covered_pages))
                final_text_coverage = (page_count - remaining) / page_count
                metadata.update(
                    {
                        "ocr_adapter": self._ocr.name,
                        "ocr_version": self._ocr.version,
                        "ocr_block_count": len(ocr_units),
                        "ocr_page_count": len(covered_pages),
                        "text_coverage": final_text_coverage,
                    }
                )
                warnings = [
                    warning
                    for warning in warnings
                    if not page_was_ocrd(warning, covered_pages)
                ]
                warnings.extend(
                    f"OCR_WARNING: {warning}"
                    for document in documents
                    for warning in document.warnings
                )

        status: Literal["succeeded", "partial"] = "partial" if warnings else "succeeded"
        body = "\n".join(unit.body for unit in units)
        extraction = ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name=parser_name,
            parser_version=self.version,
            status=status,
            quality_score=round(0.95 * final_text_coverage, 4),
            output_hash=sha256_bytes(body.encode("utf-8")),
            warnings=warnings,
            metadata=metadata,
        )
        return extraction, units
