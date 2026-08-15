from __future__ import annotations

import zipfile
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from kip.adapters.parsers.text_quality import replacement_ratio
from kip.adapters.parsers.xlsx_read import read_xlsx_range
from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.domain.text import normalize_text
from kip.errors import ParserError, ValidationError
from kip.ids import new_id, sha256_bytes, stable_id

__all__ = ["XlsxShallowParser", "read_xlsx_range"]

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _split_text(text: str, max_chars: int) -> list[str]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            line_break = text.rfind("\n", start, end)
            if line_break > start + max_chars // 2:
                end = line_break + 1
        chunks.append(text[start:end])
        start = end
    return chunks


def _tag(local: str) -> str:
    return f"{{{_MAIN_NS}}}{local}"


def _safe_zip_check(archive: zipfile.ZipFile, max_entries: int = 100000, max_uncompressed: int = 2_147_483_648, max_ratio: int = 200) -> None:
    infos = archive.infolist()
    if len(infos) > max_entries:
        raise ValidationError("XLSX has too many ZIP entries")
    total = sum(info.file_size for info in infos)
    compressed = max(1, sum(info.compress_size for info in infos))
    if total > max_uncompressed or total / compressed > max_ratio:
        raise ValidationError("XLSX decompression limits exceeded")


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    values: list[str] = []
    with archive.open(name) as stream:
        for _event, elem in ET.iterparse(stream, events=("end",)):
            if elem.tag == _tag("si"):
                texts = [node.text or "" for node in elem.iter(_tag("t"))]
                values.append("".join(texts))
                elem.clear()
    return values


def _sheet_paths(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rels: dict[str, str] = {}
    for rel in rel_root.findall(f"{{{_PACKAGE_REL_NS}}}Relationship"):
        rels[rel.attrib["Id"]] = rel.attrib["Target"]
    result: list[tuple[str, str]] = []
    for sheet in workbook.findall(f".//{{{_MAIN_NS}}}sheet"):
        name = sheet.attrib.get("name", "Sheet")
        rel_id = sheet.attrib.get(f"{{{_REL_NS}}}id")
        if not rel_id or rel_id not in rels:
            continue
        raw_target = rels[rel_id].lstrip("/")
        target = PurePosixPath(raw_target)
        if not raw_target.startswith("xl/"):
            target = PurePosixPath("xl") / target
        normalized = str(PurePosixPath(*[part for part in target.parts if part not in {".", "..", "/"}]))
        result.append((name, normalized))
    return result


def _parse_sheet(archive: zipfile.ZipFile, path: str, shared: list[str], max_chars: int) -> tuple[str | None, list[str], bool]:
    dimension: str | None = None
    values: OrderedDict[str, None] = OrderedDict()
    value_chars = 0
    truncated = False
    current_cell_type: str | None = None
    current_value: str | None = None
    current_inline: list[str] = []
    with archive.open(path) as stream:
        for event, elem in ET.iterparse(stream, events=("start", "end")):
            if event == "start" and elem.tag == _tag("c"):
                current_cell_type = elem.attrib.get("t")
                current_value = None
                current_inline = []
            elif event == "end":
                if elem.tag == _tag("dimension"):
                    dimension = elem.attrib.get("ref")
                elif elem.tag == _tag("v"):
                    current_value = elem.text or ""
                elif elem.tag == _tag("t") and current_cell_type == "inlineStr":
                    current_inline.append(elem.text or "")
                elif elem.tag == _tag("c"):
                    text: str | None = None
                    if current_cell_type == "s" and current_value and current_value.isdigit():
                        idx = int(current_value)
                        if 0 <= idx < len(shared):
                            text = shared[idx]
                    elif current_cell_type in {"inlineStr", "str"}:
                        text = "".join(current_inline) if current_inline else current_value
                    if text:
                        normalized = normalize_text(text)
                        if normalized and normalized not in values:
                            values[normalized] = None
                            value_chars += len(normalized) + 1
                            if value_chars > max_chars:
                                truncated = True
                                break
                    current_cell_type = None
                    current_value = None
                    current_inline = []
                elem.clear()
    return dimension, list(values), truncated


class XlsxShallowParser:
    name = "xlsx-shallow"
    version = "1.0"

    def __init__(self, max_chars_per_sheet: int = 240_000, max_chars_per_unit: int = 4000) -> None:
        self.max_chars_per_sheet = max_chars_per_sheet
        self.max_chars_per_unit = max_chars_per_unit

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in {".xlsx", ".xlsm"}

    def parse(self, path: Path, *, artifact_id: str, document_id: str, acl_scopes: list[str]) -> tuple[ExtractionRun, list[ContentUnit]]:
        extraction_id = new_id("ext")
        units: list[ContentUnit] = []
        warnings: list[str] = []
        aggregate_parts: list[str] = []
        total_sheets = 0
        parsed_sheets = 0
        try:
            with zipfile.ZipFile(path) as archive:
                _safe_zip_check(archive)
                shared = _read_shared_strings(archive)
                next_ordinal = 0
                sheet_paths = _sheet_paths(archive)
                total_sheets = len(sheet_paths)
                for sheet_name, sheet_path in sheet_paths:
                    try:
                        dimension, strings, truncated = _parse_sheet(
                            archive, sheet_path, shared, self.max_chars_per_sheet
                        )
                    except (KeyError, ET.ParseError) as exc:
                        warnings.append(f"sheet {sheet_name}: parse failed: {exc}")
                        continue
                    parsed_sheets += 1
                    header = f"Sheet: {sheet_name}\nUsed range: {dimension or 'unknown'}"
                    body = header + ("\n" + "\n".join(strings) if strings else "")
                    aggregate_parts.append(body)
                    chunks = _split_text(body, self.max_chars_per_unit)
                    if truncated:
                        warnings.append(f"sheet {sheet_name}: shallow text truncated")
                    chunk_start = 0
                    for chunk_index, chunk in enumerate(chunks):
                        normalized = normalize_text(chunk)
                        locator_data: dict[str, object] = {"sheet": sheet_name, "range": dimension}
                        if len(chunks) > 1:
                            locator_data.update(
                                {
                                    "chunk": chunk_index,
                                    "chunk_count": len(chunks),
                                    "char_start": chunk_start,
                                    "char_end": chunk_start + len(chunk),
                                }
                            )
                        units.append(
                            ContentUnit(
                                id=stable_id("unit", extraction_id, str(next_ordinal)),
                                extraction_id=extraction_id,
                                document_id=document_id,
                                artifact_id=artifact_id,
                                ordinal=next_ordinal,
                                unit_type="xlsx_sheet_shallow",
                                title=f"{path.name} - {sheet_name}",
                                body=chunk,
                                body_normalized=normalized,
                                lexical_text=normalized,
                                locator=EvidenceLocator(type="xlsx_sheet", data=locator_data),
                                acl_scopes=acl_scopes,
                                metadata={
                                    "sheet": sheet_name,
                                    "used_range": dimension,
                                    "shared_string_count": len(strings),
                                    "truncated": truncated,
                                    "deep_read_required_for_numbers": True,
                                    "chunk": chunk_index,
                                    "chunk_count": len(chunks),
                                },
                            )
                        )
                        next_ordinal += 1
                        chunk_start += len(chunk)
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            raise ParserError(f"XLSX parse failed: {path}: {exc}") from exc
        aggregate = "\n".join(aggregate_parts)
        # Sheets that failed to parse (corrupted part, bad relationship
        # target) and replacement characters from decode failures both
        # lower confidence below the 0.9 base a fully clean workbook keeps.
        sheet_ratio = (parsed_sheets / total_sheets) if total_sheets else 1.0
        quality = 0.9 * sheet_ratio * (1 - replacement_ratio(aggregate))
        extraction = ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name=self.name,
            parser_version=self.version,
            status="partial" if warnings else "succeeded",
            quality_score=quality if units else 0.0,
            output_hash=sha256_bytes(aggregate.encode("utf-8")),
            warnings=warnings,
            metadata={"mode": "shallow", "numeric_values_indexed": False},
        )
        return extraction, units
