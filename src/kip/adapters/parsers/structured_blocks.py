from __future__ import annotations

from dataclasses import dataclass

from kip.domain.json_types import JsonObject, JsonValue


@dataclass(frozen=True, slots=True)
class RenderedBlock:
    body: str
    metadata: JsonObject


def render_external_block(block: JsonObject) -> RenderedBlock:
    body = _first_text(block, ("text", "markdown", "content"))
    metadata: JsonObject = {}
    for key in ("style", "href", "spans", "footnoteText", "listType", "listDepth", "children"):
        value = block.get(key)
        if value is not None:
            metadata[key] = value

    table = _mapping(block.get("table"))
    if table is not None:
        metadata["table"] = table
        if not body:
            body = _render_table(table)

    legacy_rows = block.get("rows")
    if not body and legacy_rows is not None:
        body = _render_rows(legacy_rows)
        metadata["rows"] = legacy_rows

    image_data = _mapping(block.get("imageData"))
    if image_data is not None:
        filename = _text(image_data.get("filename")) or "unnamed"
        mime_type = _text(image_data.get("mimeType")) or "application/octet-stream"
        metadata["image"] = {"filename": filename, "mime_type": mime_type}
        if not body:
            body = f"[image: {filename}]"

    return RenderedBlock(body=body, metadata=metadata)


def format_external_warning(value: JsonValue) -> str:
    warning = _mapping(value)
    if warning is None:
        return str(value)
    code = _text(warning.get("code")) or "PARSER_WARNING"
    message = _text(warning.get("message")) or "parser warning"
    page = warning.get("page")
    page_label = f" page {page}" if isinstance(page, int) and not isinstance(page, bool) else ""
    return f"{code}{page_label}: {message}"


def _first_text(value: JsonObject, keys: tuple[str, ...]) -> str:
    for key in keys:
        text = _text(value.get(key))
        if text:
            return text
    return ""


def _mapping(value: JsonValue | None) -> JsonObject | None:
    match value:
        case dict() as mapping:
            return mapping
        case _:
            return None


def _text(value: JsonValue | None) -> str:
    match value:
        case str() as text:
            return text
        case _:
            return ""

def _render_table(table: JsonObject) -> str:
    cells = table.get("cells")
    return _render_rows(cells) if cells is not None else ""


def _render_rows(value: JsonValue) -> str:
    match value:
        case list() as rows:
            rendered_rows: list[str] = []
            for row in rows:
                match row:
                    case list() as cells:
                        rendered_rows.append("\t".join(_render_cell(cell) for cell in cells))
                    case _:
                        continue
            return "\n".join(rendered_rows)
        case _:
            return ""


def _render_cell(value: JsonValue) -> str:
    match value:
        case dict() as cell:
            return _text(cell.get("text"))
        case str() as text:
            return text
        case int() | float() as number:
            return str(number)
        case _:
            return ""
