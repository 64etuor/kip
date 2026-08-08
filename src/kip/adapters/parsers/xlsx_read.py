from __future__ import annotations

import re
import zipfile
from importlib import import_module
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from kip.errors import DependencyUnavailableError, ParserError, ValidationError

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_RANGE_RE = re.compile(
    r"^[A-Za-z]{1,3}[1-9][0-9]*(?::[A-Za-z]{1,3}[1-9][0-9]*)?$"
)


def _cell_range_start(cell_range: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Za-z]{1,3})([1-9][0-9]*)", cell_range.split(":", 1)[0])
    if not match:
        raise ValidationError("invalid XLSX cell range")
    column = 0
    for character in match.group(1).upper():
        column = column * 26 + ord(character) - ord("A") + 1
    return column, int(match.group(2))


def _column_letter(column: int) -> str:
    letters: list[str] = []
    while column:
        column, remainder = divmod(column - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def _safe_zip_check(
    archive: zipfile.ZipFile,
    max_entries: int = 100000,
    max_uncompressed: int = 2_147_483_648,
    max_ratio: int = 200,
) -> None:
    infos = archive.infolist()
    if len(infos) > max_entries:
        raise ValidationError("XLSX has too many ZIP entries")
    total = sum(info.file_size for info in infos)
    compressed = max(1, sum(info.compress_size for info in infos))
    if total > max_uncompressed or total / compressed > max_ratio:
        raise ValidationError("XLSX decompression limits exceeded")


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


def _hidden_dimensions(
    archive: zipfile.ZipFile,
    sheet_name: str,
) -> tuple[set[int], list[tuple[int, int]]]:
    path = _sheet_path(archive, sheet_name)
    if path is None or path not in archive.namelist():
        return set(), []
    hidden_rows: set[int] = set()
    hidden_columns: list[tuple[int, int]] = []
    with archive.open(path) as stream:
        for _event, element in ET.iterparse(stream, events=("end",)):
            hidden = element.attrib.get("hidden") in {"1", "true"}
            if element.tag == f"{{{_MAIN_NS}}}row" and hidden:
                row = element.attrib.get("r")
                if row and row.isdigit():
                    hidden_rows.add(int(row))
            elif element.tag == f"{{{_MAIN_NS}}}col" and hidden:
                minimum = element.attrib.get("min")
                maximum = element.attrib.get("max")
                if minimum and maximum and minimum.isdigit() and maximum.isdigit():
                    hidden_columns.append((int(minimum), int(maximum)))
            element.clear()
    return hidden_rows, hidden_columns


def _read_cells(
    load_workbook: Any,
    path: Path,
    sheet: str,
    cell_range: str,
    *,
    data_only: bool,
    hidden_rows: set[int],
    hidden_columns: list[tuple[int, int]],
) -> list[list[dict[str, Any]]]:
    workbook: Any = load_workbook(path, read_only=True, data_only=data_only)
    try:
        if sheet not in workbook.sheetnames:
            raise ValidationError(f"sheet does not exist: {sheet}")
        worksheet = workbook[sheet]
        rows: list[list[dict[str, Any]]] = []
        minimum_column, minimum_row = _cell_range_start(cell_range)
        for row_index, row in enumerate(worksheet[cell_range], start=minimum_row):
            rows.append(
                [
                    {
                        "coordinate": f"{_column_letter(column_index)}{row_index}",
                        "value": cell.value,
                        "data_type": getattr(cell, "data_type", "n"),
                        "number_format": getattr(cell, "number_format", "General"),
                        "is_date": bool(getattr(cell, "is_date", False)),
                        "row_hidden": row_index in hidden_rows,
                        "column_hidden": any(
                            minimum <= column_index <= maximum
                            for minimum, maximum in hidden_columns
                        ),
                    }
                    for column_index, cell in enumerate(row, start=minimum_column)
                ]
            )
        return rows
    finally:
        workbook.close()


def read_xlsx_range(
    path: Path,
    sheet: str,
    cell_range: str,
    *,
    include_formula_and_cached: bool = True,
) -> dict[str, Any]:
    if not _CELL_RANGE_RE.fullmatch(cell_range):
        raise ValidationError("invalid XLSX cell range")
    try:
        with zipfile.ZipFile(path) as archive:
            _safe_zip_check(archive)
            hidden_rows, hidden_columns = _hidden_dimensions(archive, sheet)
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ParserError(f"XLSX deep read failed: {path}: {exc}") from exc
    try:
        load_workbook = import_module("openpyxl").load_workbook
    except ImportError as exc:
        raise DependencyUnavailableError("Install the extractors extra for XLSX deep reads") from exc

    formulas = _read_cells(
        load_workbook,
        path,
        sheet,
        cell_range,
        data_only=False,
        hidden_rows=hidden_rows,
        hidden_columns=hidden_columns,
    )
    result: dict[str, Any] = {"sheet": sheet, "range": cell_range, "cells": formulas}
    if include_formula_and_cached:
        cached = _read_cells(
            load_workbook,
            path,
            sheet,
            cell_range,
            data_only=True,
            hidden_rows=hidden_rows,
            hidden_columns=hidden_columns,
        )
        for row_index, row in enumerate(result["cells"]):
            for column_index, cell in enumerate(row):
                cell["cached_value"] = cached[row_index][column_index]["value"]
    return result
