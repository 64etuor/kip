from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from kip.errors import ParserError, ValidationError

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_COORDINATE_RE = re.compile(r"^([A-Za-z]{1,3})([1-9][0-9]*)$")
MAX_EXCEL_COLUMN = 16_384
MAX_EXCEL_ROW = 1_048_576
MAX_RANGE_CELLS = 100_000


@dataclass(frozen=True, slots=True)
class CellRange:
    minimum_column: int
    minimum_row: int
    maximum_column: int
    maximum_row: int

    @property
    def cell_count(self) -> int:
        return (self.maximum_column - self.minimum_column + 1) * (
            self.maximum_row - self.minimum_row + 1
        )

    def contains(self, row: int, column: int) -> bool:
        return (
            self.minimum_row <= row <= self.maximum_row
            and self.minimum_column <= column <= self.maximum_column
        )

    @property
    def reference(self) -> str:
        start = f"{column_letter(self.minimum_column)}{self.minimum_row}"
        end = f"{column_letter(self.maximum_column)}{self.maximum_row}"
        return start if start == end else f"{start}:{end}"


@dataclass(frozen=True, slots=True)
class SheetMetadata:
    hidden_rows: frozenset[int]
    hidden_columns: tuple[tuple[int, int], ...]
    merged_ranges: tuple[CellRange, ...]
    filter_range: CellRange | None

    def column_hidden(self, column: int) -> bool:
        return any(minimum <= column <= maximum for minimum, maximum in self.hidden_columns)

    def row_filtered(self, row: int) -> bool:
        return bool(
            row in self.hidden_rows
            and self.filter_range is not None
            and self.filter_range.minimum_row < row <= self.filter_range.maximum_row
        )


def column_letter(column: int) -> str:
    letters: list[str] = []
    while column:
        column, remainder = divmod(column - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def _column_number(letters: str) -> int:
    column = 0
    for character in letters.upper():
        column = column * 26 + ord(character) - ord("A") + 1
    return column


def _coordinate(value: str) -> tuple[int, int]:
    match = _COORDINATE_RE.fullmatch(value.replace("$", ""))
    if not match:
        raise ValidationError("invalid XLSX cell range")
    return _column_number(match.group(1)), int(match.group(2))


def parse_cell_range(value: str, *, enforce_cell_limit: bool = True) -> CellRange:
    parts = value.split(":")
    if len(parts) not in {1, 2}:
        raise ValidationError("invalid XLSX cell range")
    minimum_column, minimum_row = _coordinate(parts[0])
    maximum_column, maximum_row = _coordinate(parts[-1])
    cell_range = CellRange(
        minimum_column,
        minimum_row,
        maximum_column,
        maximum_row,
    )
    if maximum_column < minimum_column or maximum_row < minimum_row:
        raise ValidationError("XLSX cell range must run from top-left to bottom-right")
    if maximum_column > MAX_EXCEL_COLUMN or maximum_row > MAX_EXCEL_ROW:
        raise ValidationError("XLSX cell range exceeds worksheet bounds")
    if enforce_cell_limit and cell_range.cell_count > MAX_RANGE_CELLS:
        raise ValidationError(
            f"XLSX cell range exceeds the {MAX_RANGE_CELLS:,}-cell read limit"
        )
    return cell_range


def _sheet_path(archive: zipfile.ZipFile, sheet_name: str) -> str | None:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships.findall(f"{{{_PACKAGE_REL_NS}}}Relationship")
    }
    for sheet in workbook.findall(f".//{{{_MAIN_NS}}}sheet"):
        if sheet.attrib.get("name") != sheet_name:
            continue
        relation_id = sheet.attrib.get(f"{{{_REL_NS}}}id")
        if not relation_id or relation_id not in targets:
            return None
        raw_target = targets[relation_id].lstrip("/")
        target = PurePosixPath(raw_target)
        if not raw_target.startswith("xl/"):
            target = PurePosixPath("xl") / target
        return str(
            PurePosixPath(
                *[part for part in target.parts if part not in {".", "..", "/"}]
            )
        )
    return None


def _source_range(reference: str) -> CellRange:
    try:
        return parse_cell_range(reference, enforce_cell_limit=False)
    except ValidationError as exc:
        raise ParserError(f"invalid XLSX worksheet range metadata: {reference}") from exc


def read_sheet_metadata(archive: zipfile.ZipFile, sheet_name: str) -> SheetMetadata:
    path = _sheet_path(archive, sheet_name)
    if path is None or path not in archive.namelist():
        return SheetMetadata(frozenset(), (), (), None)
    hidden_rows: set[int] = set()
    hidden_columns: list[tuple[int, int]] = []
    merged_ranges: list[CellRange] = []
    filter_range: CellRange | None = None
    with archive.open(path) as stream:
        for _event, element in ET.iterparse(stream, events=("end",)):
            hidden = element.attrib.get("hidden") in {"1", "true"}
            if element.tag == f"{{{_MAIN_NS}}}row" and hidden:
                row = element.attrib.get("r")
                if row and row.isdigit():
                    hidden_rows.add(int(row))
            if element.tag == f"{{{_MAIN_NS}}}col" and hidden:
                minimum = element.attrib.get("min")
                maximum = element.attrib.get("max")
                if minimum and maximum and minimum.isdigit() and maximum.isdigit():
                    hidden_columns.append((int(minimum), int(maximum)))
            if element.tag == f"{{{_MAIN_NS}}}mergeCell":
                reference = element.attrib.get("ref")
                if reference:
                    merged_ranges.append(_source_range(reference))
            if element.tag == f"{{{_MAIN_NS}}}autoFilter":
                reference = element.attrib.get("ref")
                if reference:
                    filter_range = _source_range(reference)
            element.clear()
    return SheetMetadata(
        frozenset(hidden_rows),
        tuple(hidden_columns),
        tuple(merged_ranges),
        filter_range,
    )
