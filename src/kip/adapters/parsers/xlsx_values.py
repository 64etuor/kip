from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from kip.domain.json_types import JsonObject, JsonValue
from kip.domain.xlsx import XlsxFormulaKind, XlsxValueType


@dataclass(frozen=True, slots=True)
class NormalizedValue:
    value: JsonValue
    value_type: XlsxValueType
    excel_serial: float | None = None

    @property
    def display_value(self) -> str | None:
        if self.value is None:
            return None
        if isinstance(self.value, bool):
            return "TRUE" if self.value else "FALSE"
        return str(self.value)


@dataclass(frozen=True, slots=True)
class FormulaDetails:
    text: str | None
    kind: XlsxFormulaKind
    reference: str | None
    attributes: JsonObject


def _iso_duration(value: timedelta) -> str:
    total_microseconds = (
        (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds
    )
    sign = "-" if total_microseconds < 0 else ""
    remaining = abs(total_microseconds)
    day_microseconds = 86_400 * 1_000_000
    hour_microseconds = 3_600 * 1_000_000
    minute_microseconds = 60 * 1_000_000
    days, remaining = divmod(remaining, day_microseconds)
    hours, remaining = divmod(remaining, hour_microseconds)
    minutes, remaining = divmod(remaining, minute_microseconds)
    seconds, microseconds = divmod(remaining, 1_000_000)
    date_part = f"{days}D" if days else ""
    time_parts: list[str] = []
    if hours:
        time_parts.append(f"{hours}H")
    if minutes:
        time_parts.append(f"{minutes}M")
    if microseconds:
        fraction = f"{microseconds:06d}".rstrip("0")
        time_parts.append(f"{seconds}.{fraction}S")
    elif seconds or (not date_part and not time_parts):
        time_parts.append(f"{seconds}S")
    time_part = f"T{''.join(time_parts)}" if time_parts else ""
    return f"{sign}P{date_part}{time_part}"


def normalize_value(
    value: Any,
    *,
    data_type: str,
    excel_serial: float | None,
) -> NormalizedValue:
    if value is None:
        return NormalizedValue(None, "blank")
    if isinstance(value, bool):
        return NormalizedValue(value, "boolean")
    if isinstance(value, datetime):
        return NormalizedValue(value.isoformat(), "datetime", excel_serial)
    if isinstance(value, date):
        return NormalizedValue(value.isoformat(), "date", excel_serial)
    if isinstance(value, time):
        return NormalizedValue(value.isoformat(), "time", excel_serial)
    if isinstance(value, timedelta):
        return NormalizedValue(_iso_duration(value), "duration", excel_serial)
    if isinstance(value, int):
        return NormalizedValue(value, "integer")
    if isinstance(value, float):
        if math.isfinite(value):
            return NormalizedValue(value, "number")
        if math.isnan(value):
            return NormalizedValue("NaN", "non_finite_number")
        return NormalizedValue("Infinity" if value > 0 else "-Infinity", "non_finite_number")
    if isinstance(value, str):
        return NormalizedValue(value, "error" if data_type == "e" else "string")
    return NormalizedValue(str(value), "unsupported")


def formula_details(value: Any, data_type: str) -> FormulaDetails | None:
    if data_type != "f":
        return None
    if isinstance(value, str):
        formula_text = value if value.startswith("=") else f"={value}"
        return FormulaDetails(formula_text, "normal", None, {})

    class_name = type(value).__name__
    raw_text = getattr(value, "text", None)
    object_text = str(raw_text).strip() if raw_text is not None else None
    if object_text and not object_text.startswith("="):
        object_text = f"={object_text}"
    reference_value = getattr(value, "ref", None)
    reference = str(reference_value) if reference_value is not None else None
    attributes = _formula_attributes(value)
    kind: XlsxFormulaKind
    if class_name == "ArrayFormula":
        kind = "array"
    elif class_name == "DataTableFormula":
        kind = "data_table"
    else:
        kind = "unknown"
    return FormulaDetails(object_text, kind, reference, attributes)


def _formula_attributes(value: Any) -> JsonObject:
    attributes: JsonObject = {}
    try:
        iterator = iter(value)
    except TypeError:
        return attributes
    for key, raw_attribute in iterator:
        attributes[str(key)] = _json_attribute(raw_attribute)
    return attributes


def _json_attribute(value: Any) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
