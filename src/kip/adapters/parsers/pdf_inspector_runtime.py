from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pdf_inspector

from kip.errors import DependencyUnavailableError

PDF_INSPECTOR_VERSION = "1.14.2"


@dataclass(frozen=True, slots=True)
class InspectorPage:
    index: int
    markdown: str
    needs_ocr: bool
    ocr_reason: str | None


@dataclass(frozen=True, slots=True)
class InspectorDocument:
    pages: tuple[InspectorPage, ...]
    pages_with_tables: frozenset[int]
    pages_with_columns: frozenset[int]
    pages_needing_ocr: frozenset[int]
    is_complex: bool


def extract_inspector_document(path: Path) -> InspectorDocument:
    try:
        installed = version("pdf-inspector")
    except PackageNotFoundError as error:
        raise DependencyUnavailableError(
            "Install the extractors extra for pdf-inspector parsing"
        ) from error
    if installed != PDF_INSPECTOR_VERSION:
        raise DependencyUnavailableError(
            f"pdf-inspector expected {PDF_INSPECTOR_VERSION}, found {installed}"
        )
    result = pdf_inspector.extract_pages_markdown(str(path))
    pages = tuple(
        InspectorPage(
            index=page.page,
            markdown=page.markdown,
            needs_ocr=page.needs_ocr,
            ocr_reason=page.ocr_reason,
        )
        for page in result.pages
    )
    if tuple(page.index for page in pages) != tuple(range(len(pages))):
        raise ValueError("pdf-inspector returned non-contiguous page indexes")
    valid_page_numbers = set(range(1, len(pages) + 1))
    reported_page_numbers = {
        *result.pages_with_tables,
        *result.pages_with_columns,
        *result.pages_needing_ocr,
    }
    if not reported_page_numbers <= valid_page_numbers:
        raise ValueError("pdf-inspector returned an out-of-range page signal")
    return InspectorDocument(
        pages=pages,
        pages_with_tables=frozenset(result.pages_with_tables),
        pages_with_columns=frozenset(result.pages_with_columns),
        pages_needing_ocr=frozenset(result.pages_needing_ocr),
        is_complex=result.is_complex,
    )
