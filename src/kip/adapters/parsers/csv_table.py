from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import ClassVar, Final

from kip.adapters.parsers.plain import decode_text_bytes
from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.domain.text import normalize_text
from kip.errors import ParserError
from kip.ids import new_id, sha256_bytes, stable_id

_SNIFF_DELIMITERS = ",;\t"
_DEFAULT_DELIMITER: Final = ","
_SNIFF_SAMPLE_CHARS: Final = 8192


def _detect_delimiter(sample: str) -> str:
    if not sample.strip():
        return _DEFAULT_DELIMITER
    try:
        return csv.Sniffer().sniff(sample, delimiters=_SNIFF_DELIMITERS).delimiter
    except csv.Error:
        return _DEFAULT_DELIMITER


def _serialize_rows(rows: list[list[str]], delimiter: str) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=delimiter, lineterminator="\n").writerows(rows)
    return buffer.getvalue().rstrip("\n")


def _chunk_data_rows(
    rows: list[list[str]], delimiter: str, max_chars: int
) -> list[tuple[int, int, list[list[str]]]]:
    """Group data rows into ~max_chars batches without splitting a row.

    Row numbers are 1-indexed with the header counted as row 1, so the first
    data row is row 2. Unlike XlsxShallowParser's plain character split, a
    chunk boundary here never falls inside a row: a data row can legitimately
    contain an embedded newline inside a quoted field, and cutting mid-row
    would corrupt that row's CSV structure in the emitted unit body.
    """
    chunks: list[tuple[int, int, list[list[str]]]] = []
    batch: list[list[str]] = []
    batch_chars = 0
    start_row = 2
    for row in rows:
        row_chars = len(delimiter.join(row)) + 1
        if batch and batch_chars + row_chars > max_chars:
            chunks.append((start_row, start_row + len(batch) - 1, batch))
            start_row += len(batch)
            batch = []
            batch_chars = 0
        batch.append(row)
        batch_chars += row_chars
    if batch:
        chunks.append((start_row, start_row + len(batch) - 1, batch))
    return chunks


class CsvTableParser:
    name = "csv-table"
    version = "1.0"
    extensions: ClassVar[set[str]] = {".csv"}

    def __init__(self, max_chars_per_unit: int = 4000) -> None:
        self.max_chars_per_unit = max_chars_per_unit

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.extensions

    def parse(
        self, path: Path, *, artifact_id: str, document_id: str, acl_scopes: list[str]
    ) -> tuple[ExtractionRun, list[ContentUnit]]:
        raw = path.read_bytes()
        decoded = decode_text_bytes(raw)
        # csv.reader raises outright ("line contains NUL") on a raw NUL
        # byte; NUL is never meaningful CSV content, so it is stripped only
        # for structural parsing. decoded.text (used above for the
        # encoding-quality calculation) is left untouched.
        csv_source = decoded.text.replace("\x00", "")
        delimiter = _detect_delimiter(csv_source[:_SNIFF_SAMPLE_CHARS])
        try:
            rows = list(csv.reader(io.StringIO(csv_source), delimiter=delimiter))
        except csv.Error as exc:
            raise ParserError(f"CSV parse failed: {path}: {exc}") from exc

        warnings = list(decoded.warnings)
        header = rows[0] if rows else []
        data_rows = rows[1:] if rows else []
        ragged_rows = [
            row_number
            for row_number, row in enumerate(data_rows, start=2)
            if header and len(row) != len(header)
        ]
        if ragged_rows:
            warnings.append(
                f"ragged rows: {len(ragged_rows)} row(s) with column count "
                f"mismatch (e.g. row {ragged_rows[0]})"
            )

        header_text = _serialize_rows([header], delimiter) if header else ""
        row_chunks = _chunk_data_rows(data_rows, delimiter, self.max_chars_per_unit)
        if not row_chunks and header:
            # Header-only file: still emit one unit so the column names stay
            # searchable even though there is no data row to attach them to.
            row_chunks = [(1, 1, [])]

        extraction_id = new_id("ext")
        units: list[ContentUnit] = []
        for ordinal, (start_row, end_row, batch) in enumerate(row_chunks):
            data_text = _serialize_rows(batch, delimiter) if batch else ""
            if header_text and data_text:
                body = f"{header_text}\n{data_text}"
            else:
                body = header_text or data_text
            normalized = normalize_text(body)
            units.append(
                ContentUnit(
                    id=stable_id("unit", extraction_id, str(ordinal)),
                    extraction_id=extraction_id,
                    document_id=document_id,
                    artifact_id=artifact_id,
                    ordinal=ordinal,
                    unit_type="csv_rows",
                    title=f"{path.name} - rows {start_row}-{end_row}",
                    body=body,
                    body_normalized=normalized,
                    # Unlike XlsxShallowParser, whose shallow shared-string
                    # index deliberately excludes numeric cell values (rule
                    # 9: never calculate spreadsheet totals from the shallow
                    # lexical index - use kip xlsx-read on the workbook
                    # instead), CSV has no formulas or derived aggregates to
                    # distrust. Every field here is already the exact source
                    # value, so indexing it verbatim is correct.
                    lexical_text=normalized,
                    locator=EvidenceLocator(
                        type="csv_rows",
                        data={"start_row": start_row, "end_row": end_row},
                    ),
                    acl_scopes=acl_scopes,
                    metadata={
                        "columns": header,
                        "delimiter": delimiter,
                        "encoding": decoded.encoding,
                    },
                )
            )

        extraction = ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name=self.name,
            parser_version=self.version,
            status="partial" if (decoded.status == "partial" or ragged_rows) else "succeeded",
            quality_score=decoded.quality,
            output_hash=sha256_bytes(csv_source.encode("utf-8")),
            warnings=warnings,
            metadata={
                "encoding": decoded.encoding,
                "delimiter": delimiter,
                "row_count": len(data_rows),
            },
        )
        return extraction, units
