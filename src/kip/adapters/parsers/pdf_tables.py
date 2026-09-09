from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol, runtime_checkable

from kip.domain.models import ContentUnit, EvidenceLocator
from kip.domain.text import normalize_text
from kip.errors import DependencyUnavailableError
from kip.ids import stable_id


class PdfTable(Protocol):
    row_count: int
    col_count: int
    bbox: tuple[float, float, float, float]

    def to_markdown(self, clean: bool) -> str: ...


class PdfTableFinder(Protocol):
    tables: list[PdfTable]


class PdfTablePage(Protocol):
    def find_tables(self, strategy: str) -> PdfTableFinder: ...


class PdfTableDocument(Protocol):
    def __getitem__(self, index: int) -> PdfTablePage: ...

    def close(self) -> None: ...


@runtime_checkable
class PymupdfModule(Protocol):
    def open(self, path: Path) -> PdfTableDocument: ...


@dataclass(frozen=True, slots=True)
class PdfTableContext:
    extraction_id: str
    artifact_id: str
    document_id: str
    acl_scopes: tuple[str, ...]
    start_ordinal: int


@dataclass(frozen=True, slots=True)
class PdfTableResult:
    units: tuple[ContentUnit, ...]
    next_ordinal: int
    warnings: tuple[str, ...] = ()


def extract_pymupdf_table_units(
    page: PdfTablePage,
    page_index: int,
    context: PdfTableContext,
) -> PdfTableResult:
    page_number = page_index + 1
    try:
        tables = list(page.find_tables(strategy="lines_strict").tables)
    except Exception as error:
        return PdfTableResult(
            units=(),
            next_ordinal=context.start_ordinal,
            warnings=(f"TABLE_DETECTION_FAILED: page {page_number}: {error}",),
        )

    units: list[ContentUnit] = []
    warnings: list[str] = []
    ordinal = context.start_ordinal
    for table_index, table in enumerate(tables):
        try:
            row_count = int(table.row_count)
            col_count = int(table.col_count)
            if row_count < 2 or col_count < 2:
                continue
            body = table.to_markdown(clean=True)
            bbox = [float(value) for value in table.bbox]
        except Exception as error:
            warnings.append(
                f"TABLE_DETECTION_FAILED: page {page_number} table {table_index}: {error}"
            )
            continue
        normalized = normalize_text(body)
        units.append(
            ContentUnit(
                id=stable_id(
                    "unit",
                    context.extraction_id,
                    f"{page_index}-table-{table_index}",
                ),
                extraction_id=context.extraction_id,
                document_id=context.document_id,
                artifact_id=context.artifact_id,
                ordinal=ordinal,
                unit_type="pdf_table",
                title=f"table {table_index + 1} - page {page_number}",
                body=body,
                body_normalized=normalized,
                lexical_text=normalized,
                locator=EvidenceLocator(
                    type="pdf_table",
                    data={
                        "page": page_number,
                        "end_page": page_number,
                        "table_index": table_index,
                    },
                ),
                acl_scopes=list(context.acl_scopes),
                metadata={
                    "row_count": row_count,
                    "col_count": col_count,
                    "strategy": "lines_strict",
                    "source": "pymupdf.find_tables",
                    "bbox": bbox,
                },
            )
        )
        ordinal += 1
    return PdfTableResult(
        units=tuple(units),
        next_ordinal=ordinal,
        warnings=tuple(warnings),
    )


def extract_pymupdf_fallback_tables(
    path: Path,
    page_indices: tuple[int, ...],
    context: PdfTableContext,
) -> PdfTableResult:
    if not page_indices:
        return PdfTableResult(units=(), next_ordinal=context.start_ordinal)
    try:
        module = import_module("pymupdf")
    except ImportError as error:
        raise DependencyUnavailableError(
            "Install the extractors extra for PDF table fallback"
        ) from error
    if not isinstance(module, PymupdfModule):
        raise DependencyUnavailableError("PyMuPDF table fallback API is unavailable")

    units: list[ContentUnit] = []
    warnings: list[str] = []
    ordinal = context.start_ordinal
    try:
        document = module.open(path)
        try:
            for page_index in page_indices:
                result = extract_pymupdf_table_units(
                    document[page_index],
                    page_index,
                    PdfTableContext(
                        extraction_id=context.extraction_id,
                        artifact_id=context.artifact_id,
                        document_id=context.document_id,
                        acl_scopes=context.acl_scopes,
                        start_ordinal=ordinal,
                    ),
                )
                units.extend(result.units)
                warnings.extend(result.warnings)
                ordinal = result.next_ordinal
        finally:
            document.close()
    except (OSError, RuntimeError, ValueError) as error:
        warnings.append(f"TABLE_DETECTION_FAILED: fallback open: {error}")
    return PdfTableResult(tuple(units), ordinal, tuple(warnings))
