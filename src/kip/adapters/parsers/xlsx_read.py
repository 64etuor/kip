from __future__ import annotations

import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from kip.adapters.parsers.xlsx_layout import (
    CellRange,
    SheetMetadata,
    column_letter,
    parse_cell_range,
    read_sheet_metadata,
)
from kip.adapters.parsers.xlsx_values import (
    FormulaDetails,
    NormalizedValue,
    formula_details,
    normalize_value,
)
from kip.adapters.parsers.zip_guard import check_zip_bomb_guard
from kip.domain.xlsx import XlsxCell, XlsxRangeData
from kip.errors import DependencyUnavailableError, ParserError, ValidationError

type ExcelDateValue = date | datetime | time | timedelta
type ToExcel = Callable[[ExcelDateValue, datetime], float]

@dataclass(frozen=True, slots=True)
class WorkbookDependencies:
    load_workbook: Callable[..., Any]
    to_excel: ToExcel


@dataclass(frozen=True, slots=True)
class WorkbookReadSpec:
    path: Path
    sheet: str
    cell_range: CellRange
    metadata: SheetMetadata
    data_only: bool


@dataclass(frozen=True, slots=True)
class CellLocation:
    cell: Any | None
    row: int
    column: int


@dataclass(frozen=True, slots=True)
class CellReadContext:
    spec: WorkbookReadSpec
    epoch: datetime
    to_excel: ToExcel
    merges: dict[tuple[int, int], CellRange]


def _merge_lookup(
    cell_range: CellRange,
    merged_ranges: tuple[CellRange, ...],
) -> dict[tuple[int, int], CellRange]:
    lookup: dict[tuple[int, int], CellRange] = {}
    for merged in merged_ranges:
        minimum_row = max(cell_range.minimum_row, merged.minimum_row)
        maximum_row = min(cell_range.maximum_row, merged.maximum_row)
        minimum_column = max(cell_range.minimum_column, merged.minimum_column)
        maximum_column = min(cell_range.maximum_column, merged.maximum_column)
        if minimum_row > maximum_row or minimum_column > maximum_column:
            continue
        for row in range(minimum_row, maximum_row + 1):
            for column in range(minimum_column, maximum_column + 1):
                lookup[(row, column)] = merged
    return lookup


def _excel_serial(value: Any, epoch: datetime, to_excel: ToExcel) -> float | None:
    if isinstance(value, datetime | date | time | timedelta):
        return float(to_excel(value, epoch))
    return None


def _cell_formula(value: Any, data_type: str) -> FormulaDetails | None:
    return formula_details(value, data_type)


def _cell_payload(
    location: CellLocation,
    context: CellReadContext,
) -> XlsxCell:
    raw_value = getattr(location.cell, "value", None)
    data_type = str(getattr(location.cell, "data_type", "n") or "n")
    formula = _cell_formula(raw_value, data_type)
    serial = _excel_serial(raw_value, context.epoch, context.to_excel)
    normalized = normalize_value(raw_value, data_type=data_type, excel_serial=serial)
    if formula is not None:
        normalized = normalize_value(
            formula.text,
            data_type="f",
            excel_serial=None,
        )
        normalized = NormalizedValue(normalized.value, "formula")
    merged = context.merges.get((location.row, location.column))
    return {
        "coordinate": f"{column_letter(location.column)}{location.row}",
        "value": normalized.value,
        "cached_value": None,
        "value_type": normalized.value_type,
        "cached_value_type": "blank",
        "display_value": normalized.display_value,
        "data_type": data_type,
        "number_format": str(getattr(location.cell, "number_format", None) or "General"),
        "is_date": bool(getattr(location.cell, "is_date", False)),
        "excel_serial": normalized.excel_serial,
        "cached_excel_serial": None,
        "formula": formula.text if formula else None,
        "formula_kind": formula.kind if formula else None,
        "formula_ref": formula.reference if formula else None,
        "formula_attributes": formula.attributes if formula else {},
        "row_hidden": location.row in context.spec.metadata.hidden_rows,
        "row_filtered": context.spec.metadata.row_filtered(location.row),
        "column_hidden": context.spec.metadata.column_hidden(location.column),
        "merged": merged is not None,
        "merge_master": (
            f"{column_letter(merged.minimum_column)}{merged.minimum_row}" if merged else None
        ),
        "merge_range": merged.reference if merged else None,
    }


def _read_cells(
    dependencies: WorkbookDependencies,
    spec: WorkbookReadSpec,
) -> list[list[XlsxCell]]:
    workbook: Any = dependencies.load_workbook(
        spec.path,
        read_only=True,
        data_only=spec.data_only,
        keep_vba=False,
        keep_links=False,
    )
    try:
        if spec.sheet not in workbook.sheetnames:
            raise ValidationError(f"sheet does not exist: {spec.sheet}")
        worksheet = workbook[spec.sheet]
        bounds = spec.cell_range
        source_rows = iter(
            worksheet.iter_rows(
                min_row=bounds.minimum_row,
                max_row=bounds.maximum_row,
                min_col=bounds.minimum_column,
                max_col=bounds.maximum_column,
            )
        )
        merges = _merge_lookup(bounds, spec.metadata.merged_ranges)
        cell_context = CellReadContext(
            spec,
            workbook.epoch,
            dependencies.to_excel,
            merges,
        )
        rows: list[list[XlsxCell]] = []
        exhausted = False
        for row_index in range(bounds.minimum_row, bounds.maximum_row + 1):
            source_row: tuple[Any, ...] = ()
            if not exhausted:
                try:
                    source_row = tuple(next(source_rows))
                except StopIteration:
                    exhausted = True
            rows.append(
                [
                    _cell_payload(
                        CellLocation(
                            source_row[column_index - bounds.minimum_column]
                            if column_index - bounds.minimum_column < len(source_row)
                            else None,
                            row_index,
                            column_index,
                        ),
                        cell_context,
                    )
                    for column_index in range(
                        bounds.minimum_column,
                        bounds.maximum_column + 1,
                    )
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
) -> XlsxRangeData:
    bounds = parse_cell_range(cell_range)
    try:
        with zipfile.ZipFile(path) as archive:
            check_zip_bomb_guard(archive, format_name="XLSX")
            metadata = read_sheet_metadata(archive, sheet)
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ParserError(f"XLSX deep read failed: {path}: {exc}") from exc
    try:
        openpyxl = import_module("openpyxl")
        openpyxl_datetime = import_module("openpyxl.utils.datetime")
    except ImportError as exc:
        raise DependencyUnavailableError("Install the extractors extra for XLSX deep reads") from exc
    dependencies = WorkbookDependencies(openpyxl.load_workbook, openpyxl_datetime.to_excel)
    formulas = _read_cells(
        dependencies,
        WorkbookReadSpec(
            path,
            sheet,
            bounds,
            metadata,
            False,
        ),
    )
    result = XlsxRangeData(sheet=sheet, range=cell_range, cells=formulas)
    if not include_formula_and_cached:
        return result
    cached = _read_cells(
        dependencies,
        WorkbookReadSpec(
            path,
            sheet,
            bounds,
            metadata,
            True,
        ),
    )
    for formula_row, cached_row in zip(result["cells"], cached, strict=True):
        for formula_cell, cached_cell in zip(formula_row, cached_row, strict=True):
            formula_cell["cached_value"] = cached_cell["value"]
            formula_cell["cached_value_type"] = cached_cell["value_type"]
            formula_cell["cached_excel_serial"] = cached_cell["excel_serial"]
            if formula_cell["formula_kind"] is not None:
                formula_cell["display_value"] = (
                    cached_cell["display_value"] or formula_cell["formula"]
                )
    return result
