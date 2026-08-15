from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Literal, Protocol, cast

from kip.domain.json_types import JsonObject
from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.domain.text import normalize_text
from kip.errors import DependencyUnavailableError, ParserError
from kip.ids import new_id, sha256_bytes, stable_id
from kip.ports.ocr import OcrDocument, OcrPort


class _PdfTable(Protocol):
    row_count: int
    col_count: int
    bbox: tuple[float, float, float, float]

    def to_markdown(self, clean: bool) -> str: ...


class _PdfTableFinder(Protocol):
    tables: list[_PdfTable]


class _PdfPage(Protocol):
    def get_text(self, option: str) -> str: ...

    def find_tables(self, strategy: str) -> _PdfTableFinder: ...


class _PdfDocument(Protocol):
    def __iter__(self) -> Iterator[_PdfPage]: ...

    def __len__(self) -> int: ...

    def close(self) -> None: ...


class _PymupdfModule(Protocol):
    def open(self, path: Path) -> _PdfDocument: ...


@dataclass(frozen=True, slots=True)
class _PdfOcrContext:
    extraction_id: str
    artifact_id: str
    document_id: str
    acl_scopes: tuple[str, ...]
    ordinal_start: int
    candidate_pages: frozenset[int]


class PdfParser:
    name = "pymupdf"
    version = "1"

    def __init__(self, ocr: OcrPort | None = None, *, tables_enabled: bool = True) -> None:
        self._ocr = ocr
        self._tables_enabled = tables_enabled

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def parse(self, path: Path, *, artifact_id: str, document_id: str, acl_scopes: list[str]) -> tuple[ExtractionRun, list[ContentUnit]]:
        try:
            pymupdf = cast(_PymupdfModule, import_module("pymupdf"))
        except ImportError as exc:
            raise DependencyUnavailableError("Install the extractors extra for PDF parsing") from exc
        # pymupdf raises its own exception hierarchy (rooted at
        # pymupdf.mupdf.FzErrorBase, e.g. FzErrorFormat for a file whose
        # content does not match its declared/expected format - a PNG's
        # magic bytes saved with a .pdf extension) instead of only the
        # stdlib OSError/RuntimeError/ValueError this parser originally
        # guarded against. Broaden the catch so any malformed-content error
        # from the library always surfaces as a typed ParserError rather
        # than an uncaught crash. Kept optional/best-effort: an older
        # pymupdf build without the pymupdf.mupdf submodule still falls
        # back to the original stdlib-only guard.
        pdf_error_types: tuple[type[BaseException], ...] = (OSError, RuntimeError, ValueError)
        with contextlib.suppress(ImportError):
            pdf_error_types = (*pdf_error_types, import_module("pymupdf.mupdf").FzErrorBase)
        extraction_id = new_id("ext")
        units: list[ContentUnit] = []
        warnings: list[str] = []
        ocr_candidates: dict[int, str] = {}
        try:
            document = pymupdf.open(path)
            try:
                # Ordinals must be unique across every unit in the extraction
                # (enforced by the ingestion repositories). Page units keep
                # their historical ordinal == page-index scheme untouched;
                # table units are numbered starting right after the last
                # page ordinal so both stay unique without needing a
                # second pass over the document.
                page_count_total = len(document)
                table_ordinal = page_count_total
                for index, page in enumerate(document):
                    text = page.get_text("text") or ""
                    normalized = normalize_text(text)
                    reason = _ocr_reason(text)
                    if reason is not None:
                        page_number = index + 1
                        ocr_candidates[page_number] = reason
                        warnings.append(
                            f"page {page_number}: OCR candidate ({reason})"
                        )
                    units.append(
                        ContentUnit(
                            id=stable_id("unit", extraction_id, str(index)),
                            extraction_id=extraction_id,
                            document_id=document_id,
                            artifact_id=artifact_id,
                            ordinal=index,
                            unit_type="pdf_page",
                            title=f"{path.name} - page {index + 1}",
                            body=text,
                            body_normalized=normalized,
                            lexical_text=normalized,
                            locator=EvidenceLocator(type="pdf_page", data={"page": index + 1}),
                            acl_scopes=acl_scopes,
                            metadata={"page": index + 1},
                        )
                    )
                    if self._tables_enabled:
                        table_units, table_ordinal = _extract_pdf_tables(
                            page,
                            page_index=index,
                            start_ordinal=table_ordinal,
                            extraction_id=extraction_id,
                            artifact_id=artifact_id,
                            document_id=document_id,
                            acl_scopes=acl_scopes,
                            warnings=warnings,
                        )
                        units.extend(table_units)
            finally:
                document.close()
        except pdf_error_types as exc:
            raise ParserError(f"PDF parse failed: {path}: {exc}") from exc
        # `len(units)` now also includes additive pdf_table units, so the
        # page-count-derived metrics below (text_coverage, quality) must use
        # the actual page total captured above rather than the unit count.
        page_count = page_count_total
        low_text_page_count = sum(
            reason == "low_text" for reason in ocr_candidates.values()
        )
        candidate_page_count = len(ocr_candidates)
        text_coverage = (
            (page_count - candidate_page_count) / page_count if page_count else 0.0
        )
        metadata: JsonObject = {
            "page_count": page_count,
            "low_text_page_count": low_text_page_count,
            "ocr_candidate_page_count": candidate_page_count,
            "ocr_candidate_reasons": {
                str(page): reason for page, reason in ocr_candidates.items()
            },
            "text_coverage": text_coverage,
        }
        parser_name = self.name
        if self._ocr is not None and ocr_candidates:
            parser_name = f"{self.name}+{self._ocr.name}"
            try:
                documents = self._ocr.recognize((path,))
            except ParserError as exc:
                warnings.append(f"OCR_FAILED: {exc}")
            else:
                ocr_units = _ocr_units(
                    documents,
                    _PdfOcrContext(
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
                remaining = max(0, candidate_page_count - len(covered_pages))
                text_coverage = (
                    (page_count - remaining) / page_count if page_count else 0.0
                )
                metadata.update(
                    {
                        "ocr_adapter": self._ocr.name,
                        "ocr_version": self._ocr.version,
                        "ocr_block_count": len(ocr_units),
                        "ocr_page_count": len(covered_pages),
                        "text_coverage": text_coverage,
                    }
                )
                warnings = [
                    warning
                    for warning in warnings
                    if not _page_was_ocrd(warning, covered_pages)
                ]
                warnings.extend(
                    f"OCR_WARNING: {warning}"
                    for document in documents
                    for warning in document.warnings
                )
        body = "\n".join(unit.body for unit in units)
        status: Literal["succeeded", "partial"] = "partial" if warnings else "succeeded"
        quality = round(0.95 * text_coverage, 4)
        extraction = ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name=parser_name,
            parser_version=self.version,
            status=status,
            quality_score=quality,
            output_hash=sha256_bytes(body.encode("utf-8")),
            warnings=warnings,
            metadata=metadata,
        )
        return extraction, units


def _ocr_units(
    documents: tuple[OcrDocument, ...],
    context: _PdfOcrContext,
) -> list[ContentUnit]:
    units: list[ContentUnit] = []
    for document in documents:
        for block in document.blocks:
            if block.page not in context.candidate_pages:
                continue
            if not block.text.strip():
                continue
            ordinal = context.ordinal_start + len(units)
            normalized = normalize_text(block.text)
            units.append(
                ContentUnit(
                    id=stable_id("unit", context.extraction_id, str(ordinal)),
                    extraction_id=context.extraction_id,
                    document_id=context.document_id,
                    artifact_id=context.artifact_id,
                    ordinal=ordinal,
                    unit_type="pdf_ocr",
                    title=f"{document.source_path.name} - page {block.page or 1} OCR",
                    body=block.text,
                    body_normalized=normalized,
                    lexical_text=normalized,
                    locator=EvidenceLocator(
                        type="pdf_ocr",
                        data={"page": block.page or 1, "bbox": block.bbox},
                    ),
                    acl_scopes=list(context.acl_scopes),
                    metadata={"block_type": block.block_type, **block.metadata},
                )
            )
    return units


def _extract_pdf_tables(
    page: _PdfPage,
    *,
    page_index: int,
    start_ordinal: int,
    extraction_id: str,
    artifact_id: str,
    document_id: str,
    acl_scopes: list[str],
    warnings: list[str],
) -> tuple[list[ContentUnit], int]:
    """Additively detect bordered tables on one page via PyMuPDF's own
    ``find_tables(strategy="lines_strict")``.

    Measured side-effect-free against ``page.get_text()`` (byte-identical
    before/after on 100% of a synthetic + real corpus) so it never changes
    ``pdf_page`` unit output. Table detection is best-effort: any failure
    degrades to a ``TABLE_DETECTION_FAILED`` warning instead of failing the
    page or the document, mirroring the existing ``OCR_FAILED:`` pattern.
    """
    page_number = page_index + 1
    try:
        finder = page.find_tables(strategy="lines_strict")
        tables = list(finder.tables)
    except Exception as exc:  # pragma: no cover - defensive, see docstring
        warnings.append(f"TABLE_DETECTION_FAILED: page {page_number}: {exc}")
        return [], start_ordinal

    units: list[ContentUnit] = []
    ordinal = start_ordinal
    for table_index, table in enumerate(tables):
        try:
            row_count = int(table.row_count)
            col_count = int(table.col_count)
            # This filter is what suppresses the measured real-corpus false
            # positives: decorative single-row callout/footer boxes that
            # PyMuPDF's line-based strategy otherwise detects as 1xN "tables".
            if row_count < 2 or col_count < 2:
                continue
            body = table.to_markdown(clean=True)
            bbox = [float(value) for value in table.bbox]
        except Exception as exc:  # pragma: no cover - defensive, see docstring
            warnings.append(
                f"TABLE_DETECTION_FAILED: page {page_number} table {table_index}: {exc}"
            )
            continue
        normalized = normalize_text(body)
        units.append(
            ContentUnit(
                id=stable_id("unit", extraction_id, f"{page_index}-table-{table_index}"),
                extraction_id=extraction_id,
                document_id=document_id,
                artifact_id=artifact_id,
                ordinal=ordinal,
                unit_type="pdf_table",
                title=f"table {table_index + 1} - page {page_number}",
                body=body,
                body_normalized=normalized,
                lexical_text=normalized,
                locator=EvidenceLocator(
                    type="pdf_table",
                    data={
                        # PyMuPDF's find_tables never merges a table across a
                        # page break, so end_page always equals page today;
                        # the field is kept so a future cross-page merge can
                        # populate it without a locator shape change.
                        "page": page_number,
                        "end_page": page_number,
                        "table_index": table_index,
                    },
                ),
                acl_scopes=acl_scopes,
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
    return units, ordinal


def _page_was_ocrd(warning: str, covered_pages: set[int | None]) -> bool:
    if not warning.startswith("page "):
        return False
    page_text = warning.removeprefix("page ").split(":", maxsplit=1)[0]
    return page_text.isdigit() and int(page_text) in covered_pages


def _ocr_reason(text: str) -> str | None:
    characters = [character for character in text if character not in " \t\n\r"]
    total = len(characters)
    if total < 20:
        return "low_text"
    pua = sum(
        "\ue000" <= character <= "\uf8ff"
        or "\U000f0000" <= character <= "\U000ffffd"
        or "\U00100000" <= character <= "\U0010fffd"
        for character in characters
    )
    control = sum(
        ord(character) < 0x20
        or ord(character) == 0x7F
        or 0x80 <= ord(character) <= 0x9F
        for character in characters
    )
    replacement = characters.count("\ufffd")
    if pua / total >= 0.2:
        return "high_pua"
    if control / total >= 0.05:
        return "high_control"
    if replacement / total >= 0.05:
        return "high_replacement"
    return None
