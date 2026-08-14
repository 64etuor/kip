from __future__ import annotations

from typing import Literal, TypedDict

from kip.domain.json_types import JsonObject, JsonValue

type XlsxFormulaKind = Literal["normal", "array", "data_table", "unknown"]
type XlsxValueType = Literal[
    "blank",
    "boolean",
    "date",
    "datetime",
    "duration",
    "error",
    "formula",
    "integer",
    "non_finite_number",
    "number",
    "string",
    "time",
    "unsupported",
]


class XlsxCell(TypedDict):
    coordinate: str
    value: JsonValue
    cached_value: JsonValue
    value_type: XlsxValueType
    cached_value_type: XlsxValueType
    display_value: str | None
    data_type: str
    number_format: str
    is_date: bool
    excel_serial: float | None
    cached_excel_serial: float | None
    formula: str | None
    formula_kind: XlsxFormulaKind | None
    formula_ref: str | None
    formula_attributes: JsonObject
    row_hidden: bool
    row_filtered: bool
    column_hidden: bool
    merged: bool
    merge_master: str | None
    merge_range: str | None


class XlsxRangeData(TypedDict):
    sheet: str
    range: str
    cells: list[list[XlsxCell]]
