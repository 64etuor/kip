from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from xml.etree import ElementTree as ET

from kip.adapters.parsers.zip_guard import check_zip_bomb_guard
from kip.domain.json_types import JsonObject, JsonValue
from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.domain.text import normalize_text
from kip.errors import ParserError
from kip.ids import new_id, sha256_bytes, stable_id

# Design note: this parser stays on the raw zipfile+ElementTree walk used by the
# xlsx/pptx-ooxml adapters instead of layering python-docx's object model on top.
# The unit shapes this task requires (paragraph-range chunk locators, dedicated
# table/textbox/header-footer units, mc:AlternateContent de-duplication, per-part
# isolation for malformed header/footer parts) all need direct control over which
# XML subtrees are visited and in what order. python-docx's Paragraph/Table
# wrappers would have to be bypassed for header/footer parts, text boxes, and
# relationship resolution anyway, so building a second per-paragraph object graph
# on top of it would add weight without buying back any of the structural control
# this walk needs.

_W_NS: Final = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS: Final = "http://schemas.openxmlformats.org/package/2006/relationships"

# Word only defines heading levels 1-9; drop the former two-digit branch
# ("[1-9][0-9]?") so a bogus style id like "Heading99" cannot yield a
# nonsensical level 99.
_HEADING_STYLE_RE: Final = re.compile(r"^Heading\s*([1-9])$", re.IGNORECASE)
_HEADER_FOOTER_RE: Final = re.compile(r"^word/(header|footer)\d*\.xml$")
# word/footnotes.xml and word/endnotes.xml are fixed, singular part names per
# the OOXML WordprocessingML schema (unlike headers/footers, which are
# numbered per section) - one part holds every footnote in the document, the
# other every endnote.
_FOOTNOTES_PART: Final = "word/footnotes.xml"
_ENDNOTES_PART: Final = "word/endnotes.xml"
# w:footnote/w:endnote elements with these w:type values are Word-generated
# separator marks (the divider line and its continuation-page variant), not
# author content. They render as a blank paragraph containing only a
# <w:separator/> or <w:continuationSeparator/> child, so _walk_body's normal
# text walk already turns them into empty text; this set of "skip" types is
# a comment marker for that behavior, and the code below explicitly skips
# them so a malformed part where an author-authored note happens to reuse
# one of these type values can't be miscategorized.
_NOTE_SEPARATOR_TYPES: Final = frozenset({"separator", "continuationSeparator", "continuationNotice"})
_REPLACEMENT_CHAR: Final = "�"
_DEFAULT_MAX_CHARS_PER_UNIT: Final = 4000
# ET.fromstring accepts arbitrarily deep XML nesting (expat parses
# iteratively), but the run/textbox walkers below are hand-written Python
# recursion over the resulting Element tree. A crafted document.xml with a
# few thousand nested wrapper elements around a single run - small on disk,
# well under the zip-bomb ratio/size guard - hits CPython's default
# recursion limit and crashes the whole parse with an uncaught
# RecursionError instead of a clean, caught ParserError. This cap keeps the
# walk inside a safe budget; legitimate DOCX content (paragraphs, runs,
# hyperlinks, a handful of smart-tag/proofing wrappers) never nests this
# deep.
_MAX_ELEMENT_DEPTH: Final = 500


def _w(local: str) -> str:
    return f"{{{_W_NS}}}{local}"


def _r_attr(local: str) -> str:
    return f"{{{_R_NS}}}{local}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass(frozen=True, slots=True)
class _Relationship:
    target: str
    type: str
    mode: str


@dataclass(slots=True)
class _ParagraphRecord:
    number: int
    text: str
    heading_level: int | None
    links: list[JsonValue]


@dataclass(slots=True)
class _TableRender:
    body: str
    row_count: int
    col_count: int
    nested_table_count: int = 0


@dataclass(frozen=True, slots=True)
class _DocxContext:
    extraction_id: str
    artifact_id: str
    document_id: str | None
    acl_scopes: list[str]
    path: Path
    rels: dict[str, _Relationship]


@dataclass(slots=True)
class _ContentTypes:
    defaults: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, str] = field(default_factory=dict)


class DocxParser:
    name = "docx-xml"
    version = "2.0"

    def __init__(self, max_chars_per_unit: int = _DEFAULT_MAX_CHARS_PER_UNIT) -> None:
        self.max_chars_per_unit = max_chars_per_unit

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".docx"

    def parse(
        self,
        path: Path,
        *,
        artifact_id: str,
        document_id: str,
        acl_scopes: list[str],
    ) -> tuple[ExtractionRun, list[ContentUnit]]:
        extraction_id = new_id("ext")
        warnings: list[str] = []
        try:
            with zipfile.ZipFile(path) as archive:
                check_zip_bomb_guard(archive, format_name="DOCX")
                names = set(archive.namelist())
                document_root = ET.fromstring(archive.read("word/document.xml"))
                body = document_root.find(_w("body"))
                if body is None:
                    raise ParserError(f"DOCX parse failed: {path}: missing document body")
                rels = _read_relationships(archive, "word/_rels/document.xml.rels")
                content_types = _read_content_types(archive)
                paragraphs, tables = _walk_body(body, rels)
                textboxes, nested_textbox_count = _collect_textboxes(body)
                header_footer_texts = _read_header_footer_parts(archive, names, warnings)
                footnotes = _read_notes_part(archive, names, _FOOTNOTES_PART, "footnote", warnings)
                endnotes = _read_notes_part(archive, names, _ENDNOTES_PART, "endnote", warnings)
                images = _collect_image_relationships(rels, content_types)
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            raise ParserError(f"DOCX parse failed: {path}: {exc}") from exc

        context = _DocxContext(
            extraction_id=extraction_id,
            artifact_id=artifact_id,
            document_id=document_id,
            acl_scopes=acl_scopes,
            path=path,
            rels=rels,
        )

        units: list[ContentUnit] = []
        for chunk in _chunk_paragraphs(paragraphs, self.max_chars_per_unit):
            unit = _paragraph_chunk_unit(chunk, context, ordinal=len(units))
            if unit is not None:
                units.append(unit)
        table_renders: list[_TableRender] = []
        for index, table in enumerate(tables):
            rendered = _render_table(table, context.rels)
            table_renders.append(rendered)
            units.append(_table_unit_from_render(rendered, index, context, ordinal=len(units)))
        for index, txbx_content in enumerate(textboxes):
            unit = _textbox_unit(txbx_content, index, context, ordinal=len(units))
            if unit is not None:
                units.append(unit)
        for part_name, text in header_footer_texts:
            unit = _header_footer_unit(part_name, text, context, ordinal=len(units))
            if unit is not None:
                units.append(unit)
        for note_id, text in footnotes:
            unit = _note_unit("footnote", note_id, text, context, ordinal=len(units))
            if unit is not None:
                units.append(unit)
        for note_id, text in endnotes:
            unit = _note_unit("endnote", note_id, text, context, ordinal=len(units))
            if unit is not None:
                units.append(unit)

        aggregate = "\n".join(unit.body for unit in units)
        quality = _compute_quality(paragraphs, aggregate) if units else 0.0
        nested_table_count = sum(rendered.nested_table_count for rendered in table_renders)
        extraction = ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name=self.name,
            parser_version=self.version,
            status="partial" if warnings else "succeeded",
            quality_score=quality,
            output_hash=sha256_bytes(aggregate.encode("utf-8")),
            warnings=warnings,
            metadata={
                "paragraph_count": len(paragraphs),
                "table_count": len(tables),
                "nested_table_count": nested_table_count,
                "textbox_count": len(textboxes),
                "nested_textbox_count": nested_textbox_count,
                "footnote_count": len(footnotes),
                "endnote_count": len(endnotes),
                "image_count": len(images),
                "images": images,
            },
        )
        return extraction, units


# --- relationship + content-type resolution -------------------------------------------------


def _read_relationships(archive: zipfile.ZipFile, rels_path: str) -> dict[str, _Relationship]:
    if rels_path not in archive.namelist():
        return {}
    try:
        root = ET.fromstring(archive.read(rels_path))
    except ET.ParseError:
        return {}
    rels: dict[str, _Relationship] = {}
    for rel in root.findall(f"{{{_PKG_REL_NS}}}Relationship"):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if not rel_id or not target:
            continue
        rels[rel_id] = _Relationship(
            target=target,
            type=rel.attrib.get("Type", ""),
            mode=rel.attrib.get("TargetMode", "Internal"),
        )
    return rels


def _read_content_types(archive: zipfile.ZipFile) -> _ContentTypes:
    name = "[Content_Types].xml"
    if name not in archive.namelist():
        return _ContentTypes()
    try:
        root = ET.fromstring(archive.read(name))
    except ET.ParseError:
        return _ContentTypes()
    content_types = _ContentTypes()
    for elem in root:
        tag = _local_name(elem.tag)
        if tag == "Default":
            extension = elem.attrib.get("Extension", "").lower()
            content_type = elem.attrib.get("ContentType", "")
            if extension:
                content_types.defaults[extension] = content_type
        elif tag == "Override":
            part_name = elem.attrib.get("PartName", "")
            content_type = elem.attrib.get("ContentType", "")
            if part_name:
                content_types.overrides[part_name] = content_type
    return content_types


def _resolve_target(source_part: str, target: str) -> str:
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))
    return resolved.lstrip("/")


def _content_type_for(part_path: str, content_types: _ContentTypes) -> str:
    part_name = "/" + part_path if not part_path.startswith("/") else part_path
    if part_name in content_types.overrides:
        return content_types.overrides[part_name]
    extension = part_path.rsplit(".", 1)[-1].lower() if "." in part_path else ""
    return content_types.defaults.get(extension, "application/octet-stream")


def _collect_image_relationships(
    rels: dict[str, _Relationship], content_types: _ContentTypes
) -> list[JsonObject]:
    images: list[JsonObject] = []
    for rel in rels.values():
        if rel.mode == "External" or not rel.type.endswith("/image"):
            continue
        part_path = _resolve_target("word/document.xml", rel.target)
        images.append(
            {
                "target": part_path,
                "content_type": _content_type_for(part_path, content_types),
            }
        )
    return images


# --- body walk: paragraphs, headings, list markers, hyperlinks, tables ----------------------


def _walk_body(
    body: ET.Element, rels: dict[str, _Relationship]
) -> tuple[list[_ParagraphRecord], list[ET.Element]]:
    paragraphs: list[_ParagraphRecord] = []
    tables: list[ET.Element] = []
    number = 0
    for child in _resolve_body_children(body):
        tag = _local_name(child.tag)
        if tag == "p":
            number += 1
            paragraphs.append(_paragraph_record(child, number, rels))
        elif tag == "tbl":
            tables.append(child)
    return paragraphs, tables


def _resolve_body_children(body: ET.Element) -> list[ET.Element]:
    """Flatten mc:AlternateContent-wrapped body children into their content.

    Word occasionally wraps a body-level paragraph or table in
    mc:AlternateContent (e.g. content that differs between application
    versions), the same construct already handled for text boxes. A direct
    `for child in body` walk would see only the wrapper element - neither
    "p" nor "tbl" - and silently drop the paragraph/table inside. Take
    Choice's direct children, else Fallback's, exactly as the text box
    Choice/Fallback resolution does.
    """
    resolved: list[ET.Element] = []
    for child in body:
        if _local_name(child.tag) != "AlternateContent":
            resolved.append(child)
            continue
        choice = _first_child_local(child, "Choice")
        chosen = list(choice) if choice is not None else []
        if not chosen:
            fallback = _first_child_local(child, "Fallback")
            chosen = list(fallback) if fallback is not None else []
        resolved.extend(chosen)
    return resolved


def _paragraph_record(
    paragraph: ET.Element, number: int, rels: dict[str, _Relationship]
) -> _ParagraphRecord:
    text, links = _extract_text_and_links(paragraph, rels)
    style_id = _paragraph_style_id(paragraph)
    heading_level = _heading_level(style_id)
    if _has_num_pr(paragraph) and text.strip():
        text = f"- {text}"
    return _ParagraphRecord(number=number, text=text, heading_level=heading_level, links=links)


def _paragraph_style_id(paragraph: ET.Element) -> str | None:
    ppr = paragraph.find(_w("pPr"))
    if ppr is None:
        return None
    pstyle = ppr.find(_w("pStyle"))
    if pstyle is None:
        return None
    return pstyle.attrib.get(_w("val")) or None


def _has_num_pr(paragraph: ET.Element) -> bool:
    ppr = paragraph.find(_w("pPr"))
    if ppr is None:
        return False
    return ppr.find(_w("numPr")) is not None


def _heading_level(style_id: str | None) -> int | None:
    if not style_id:
        return None
    match = _HEADING_STYLE_RE.match(style_id)
    if match:
        return int(match.group(1))
    if style_id.lower() == "title":
        return 0
    return None


def _extract_text_and_links(
    elem: ET.Element, rels: dict[str, _Relationship]
) -> tuple[str, list[JsonValue]]:
    texts: list[str] = []
    links: list[JsonValue] = []
    for child in elem:
        _walk_run(child, texts, links, rels, depth=0)
    return "".join(texts), links


def _walk_run(
    elem: ET.Element,
    texts: list[str],
    links: list[JsonValue],
    rels: dict[str, _Relationship],
    *,
    depth: int,
) -> None:
    if depth > _MAX_ELEMENT_DEPTH:
        raise ParserError(
            f"DOCX element nesting exceeds {_MAX_ELEMENT_DEPTH} levels"
        )
    tag = _local_name(elem.tag)
    # Text boxes are extracted as their own docx_textbox units; skip their
    # descendants here so body text never duplicates textbox content.
    if tag == "txbxContent":
        return
    if tag == "t":
        texts.append(elem.text or "")
        return
    if tag == "tab":
        texts.append("\t")
        return
    if tag in ("br", "cr"):
        texts.append("\n")
        return
    if tag == "noBreakHyphen":
        # <w:noBreakHyphen/> renders as a literal "-" glyph (just one Word
        # won't break across a line wrap); it carries no <w:t> child, so the
        # generic recursion below silently drops it entirely, gluing the
        # words on either side together (e.g. "Non-break" + "hyphen test."
        # -> "Non-breakhyphen test.", losing the hyphen and looking like a
        # single misspelled word). Emit the visible character instead.
        texts.append("-")
        return
    if tag == "hyperlink":
        start = len(texts)
        for child in elem:
            _walk_run(child, texts, links, rels, depth=depth + 1)
        anchor_text = "".join(texts[start:])
        rel_id = elem.attrib.get(_r_attr("id"))
        if rel_id and anchor_text.strip():
            relationship = rels.get(rel_id)
            if relationship is not None:
                links.append({"text": anchor_text, "target": relationship.target})
        return
    for child in elem:
        _walk_run(child, texts, links, rels, depth=depth + 1)


# --- text boxes: mc:AlternateContent Choice/Fallback de-duplication -------------------------


def _collect_textboxes(body: ET.Element) -> tuple[list[ET.Element], int]:
    found: list[ET.Element] = []
    nested_count = _walk_textboxes(body, found, depth=0, inside_textbox=False)
    return found, nested_count


def _walk_textboxes(
    elem: ET.Element, out: list[ET.Element], *, depth: int, inside_textbox: bool = False
) -> int:
    """Collect every text box, including one nested inside another.

    Returns the count of text boxes discovered while ``inside_textbox`` is
    already true - i.e. a text box (directly, or via a table cell) inside
    another text box's content - for extraction metadata.
    """
    if depth > _MAX_ELEMENT_DEPTH:
        raise ParserError(
            f"DOCX element nesting exceeds {_MAX_ELEMENT_DEPTH} levels"
        )
    tag = _local_name(elem.tag)
    if tag == "AlternateContent":
        # Word emits the same text box twice under mc:AlternateContent: once as
        # DrawingML (wps:txbx) in mc:Choice, once as legacy VML (v:textbox) in
        # mc:Fallback. Take exactly one representation to avoid counting the
        # same text box twice.
        choice = _first_child_local(elem, "Choice")
        chosen = _descendant_textboxes(choice) if choice is not None else []
        if not chosen:
            fallback = _first_child_local(elem, "Fallback")
            chosen = _descendant_textboxes(fallback) if fallback is not None else []
        out.extend(chosen)
        return len(chosen) if inside_textbox else 0
    if tag == "txbxContent":
        out.append(elem)
        nested_count = 1 if inside_textbox else 0
        # Keep walking this text box's own content instead of returning
        # immediately: a text box can itself contain a table whose cell
        # holds another, independently-nested text box, and that inner text
        # box must still be discovered as its own unit.
        for child in elem:
            nested_count += _walk_textboxes(child, out, depth=depth + 1, inside_textbox=True)
        return nested_count
    nested_count = 0
    for child in elem:
        nested_count += _walk_textboxes(child, out, depth=depth + 1, inside_textbox=inside_textbox)
    return nested_count


def _descendant_textboxes(elem: ET.Element) -> list[ET.Element]:
    return [node for node in elem.iter() if _local_name(node.tag) == "txbxContent"]


def _first_child_local(elem: ET.Element, local: str) -> ET.Element | None:
    for child in elem:
        if _local_name(child.tag) == local:
            return child
    return None


def _textbox_text(
    txbx_content: ET.Element, rels: dict[str, _Relationship]
) -> tuple[str, list[JsonValue]]:
    parts: list[str] = []
    links: list[JsonValue] = []
    for child in txbx_content:
        tag = _local_name(child.tag)
        if tag == "p":
            text, para_links = _extract_text_and_links(child, rels)
            parts.append(text)
            links.extend(para_links)
        elif tag == "tbl":
            # A text box can itself contain a table (e.g. a callout box
            # with a small data grid). Render it the same way a body-level
            # table is rendered instead of silently dropping it; any text
            # box nested inside one of *its* cells is still collected
            # separately by _walk_textboxes and excluded here via the
            # txbxContent early-return in _walk_run.
            rendered = _render_table(child, rels)
            if rendered.body:
                parts.append(rendered.body)
    return "\n".join(parts), links


# --- tables: gridSpan/vMerge aware tab-delimited rendering ----------------------------------


def _render_table(
    table: ET.Element, rels: dict[str, _Relationship], *, depth: int = 0
) -> _TableRender:
    if depth > _MAX_ELEMENT_DEPTH:
        raise ParserError(
            f"DOCX table nesting exceeds {_MAX_ELEMENT_DEPTH} levels"
        )
    rows: list[list[str]] = []
    max_cols = 0
    nested_table_count = 0
    for tr in table:
        if _local_name(tr.tag) != "tr":
            continue
        cells: list[str] = []
        for tc in tr:
            if _local_name(tc.tag) != "tc":
                continue
            span = _grid_span(tc)
            if _is_vmerge_continuation(tc):
                text = ""
            else:
                text, cell_nested_count = _cell_text(tc, rels, depth=depth + 1)
                nested_table_count += cell_nested_count
            cells.append(text)
            cells.extend([""] * max(0, span - 1))
        rows.append(cells)
        max_cols = max(max_cols, len(cells))
    body = "\n".join("\t".join(row) for row in rows)
    return _TableRender(
        body=body, row_count=len(rows), col_count=max_cols, nested_table_count=nested_table_count
    )


def _grid_span(tc: ET.Element) -> int:
    tc_pr = tc.find(_w("tcPr"))
    if tc_pr is None:
        return 1
    grid_span = tc_pr.find(_w("gridSpan"))
    if grid_span is None:
        return 1
    value = grid_span.attrib.get(_w("val"))
    return int(value) if value and value.isdigit() else 1


def _is_vmerge_continuation(tc: ET.Element) -> bool:
    tc_pr = tc.find(_w("tcPr"))
    if tc_pr is None:
        return False
    vmerge = tc_pr.find(_w("vMerge"))
    if vmerge is None:
        return False
    value = vmerge.attrib.get(_w("val"))
    # An absent w:val means "continue" per the OOXML default; only an explicit
    # "restart" starts a new merge region and keeps its own text.
    return value is None or value.lower() != "restart"


def _cell_text(
    tc: ET.Element, rels: dict[str, _Relationship], *, depth: int
) -> tuple[str, int]:
    """Render a cell's paragraphs and any table(s) nested directly inside it.

    Returns the rendered text and the count of nested tables found (this
    cell's own nested tables plus theirs, recursively), so a table-in-a-cell
    -in-a-table is fully recovered instead of the innermost table silently
    disappearing.
    """
    parts: list[str] = []
    nested_table_count = 0
    for child in tc:
        tag = _local_name(child.tag)
        if tag == "p":
            text, _links = _extract_text_and_links(child, rels)
            parts.append(text)
        elif tag == "tbl":
            nested = _render_table(child, rels, depth=depth)
            nested_table_count += 1 + nested.nested_table_count
            if nested.body:
                parts.append(nested.body)
    return "\n".join(parts), nested_table_count


# --- header/footer parts: per-part isolation -------------------------------------------------


def _read_header_footer_parts(
    archive: zipfile.ZipFile, names: set[str], warnings: list[str]
) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for name in sorted(part for part in names if _HEADER_FOOTER_RE.match(part)):
        try:
            root = ET.fromstring(archive.read(name))
        except (KeyError, ET.ParseError) as exc:
            warnings.append(f"PARTIAL_PARSE {name}: {exc}")
            continue
        rels = _read_relationships(archive, _rels_path_for(name))
        paragraphs, tables = _walk_body(root, rels)
        parts = [record.text for record in paragraphs if record.text.strip()]
        for table in tables:
            rendered = _render_table(table, rels)
            if rendered.body.strip():
                parts.append(rendered.body)
        text = "\n".join(parts)
        if text.strip():
            results.append((name, text))
    return results


def _rels_path_for(part_name: str) -> str:
    directory, filename = posixpath.split(part_name)
    return posixpath.join(directory, "_rels", f"{filename}.rels")


# --- footnotes/endnotes: per-note units so a footnoteReference/endnoteReference -------------
# in the body can be traced to its actual text instead of the reference mark
# alone. Word stores every footnote in a single word/footnotes.xml part and
# every endnote in a single word/endnotes.xml part (not one part per note, and
# not numbered like headers/footers), keyed by a w:id that the body's
# w:footnoteReference/w:endnoteReference elements point back to.


def _read_notes_part(
    archive: zipfile.ZipFile,
    names: set[str],
    part_name: str,
    tag_name: str,
    warnings: list[str],
) -> list[tuple[str, str]]:
    if part_name not in names:
        return []
    try:
        root = ET.fromstring(archive.read(part_name))
    except (KeyError, ET.ParseError) as exc:
        warnings.append(f"PARTIAL_PARSE {part_name}: {exc}")
        return []
    rels = _read_relationships(archive, _rels_path_for(part_name))
    results: list[tuple[str, str]] = []
    for note in root.findall(_w(tag_name)):
        if note.attrib.get(_w("type")) in _NOTE_SEPARATOR_TYPES:
            continue
        note_id = note.attrib.get(_w("id"), "")
        paragraphs, tables = _walk_body(note, rels)
        parts = [record.text for record in paragraphs if record.text.strip()]
        for table in tables:
            rendered = _render_table(table, rels)
            if rendered.body.strip():
                parts.append(rendered.body)
        text = "\n".join(parts)
        if text.strip():
            results.append((note_id, text))
    return results


# --- unit construction -----------------------------------------------------------------------


def _chunk_paragraphs(
    paragraphs: list[_ParagraphRecord], max_chars: int
) -> list[list[_ParagraphRecord]]:
    chunks: list[list[_ParagraphRecord]] = []
    current: list[_ParagraphRecord] = []
    current_len = 0
    for record in paragraphs:
        piece_len = len(record.text) + 1
        if current and current_len + piece_len > max_chars:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(record)
        current_len += piece_len
    if current:
        chunks.append(current)
    return chunks


def _paragraph_chunk_unit(
    chunk: list[_ParagraphRecord], context: _DocxContext, *, ordinal: int
) -> ContentUnit | None:
    body = "\n".join(record.text for record in chunk)
    normalized = normalize_text(body)
    if not normalized:
        return None
    headings: list[JsonValue] = [
        {"text": record.text, "level": record.heading_level, "paragraph": record.number}
        for record in chunk
        if record.heading_level is not None
    ]
    links: list[JsonValue] = []
    for record in chunk:
        links.extend(record.links)
    start = chunk[0].number
    end = chunk[-1].number
    metadata: JsonObject = {
        "headings": headings,
        "links": links,
        "paragraph_count": len(chunk),
    }
    return ContentUnit(
        id=stable_id("unit", context.extraction_id, str(ordinal)),
        extraction_id=context.extraction_id,
        document_id=context.document_id,
        artifact_id=context.artifact_id,
        ordinal=ordinal,
        unit_type="docx_paragraph",
        title=f"{context.path.name} - paragraphs {start}-{end}",
        body=body,
        body_normalized=normalized,
        lexical_text=normalized,
        locator=EvidenceLocator(
            type="docx_paragraphs", data={"start_paragraph": start, "end_paragraph": end}
        ),
        acl_scopes=context.acl_scopes,
        metadata=metadata,
    )


def _table_unit_from_render(
    rendered: _TableRender, index: int, context: _DocxContext, *, ordinal: int
) -> ContentUnit:
    normalized = normalize_text(rendered.body)
    return ContentUnit(
        id=stable_id("unit", context.extraction_id, str(ordinal)),
        extraction_id=context.extraction_id,
        document_id=context.document_id,
        artifact_id=context.artifact_id,
        ordinal=ordinal,
        unit_type="docx_table",
        title=f"{context.path.name} - table {index + 1}",
        body=rendered.body,
        body_normalized=normalized,
        lexical_text=normalized,
        locator=EvidenceLocator(type="docx_table", data={"table_index": index}),
        acl_scopes=context.acl_scopes,
        metadata={
            "row_count": rendered.row_count,
            "col_count": rendered.col_count,
            "nested_table_count": rendered.nested_table_count,
        },
    )


def _textbox_unit(
    txbx_content: ET.Element, index: int, context: _DocxContext, *, ordinal: int
) -> ContentUnit | None:
    body, links = _textbox_text(txbx_content, context.rels)
    normalized = normalize_text(body)
    if not normalized:
        return None
    metadata: JsonObject = {"links": links} if links else {}
    return ContentUnit(
        id=stable_id("unit", context.extraction_id, str(ordinal)),
        extraction_id=context.extraction_id,
        document_id=context.document_id,
        artifact_id=context.artifact_id,
        ordinal=ordinal,
        unit_type="docx_textbox",
        title=f"{context.path.name} - textbox {index + 1}",
        body=body,
        body_normalized=normalized,
        lexical_text=normalized,
        locator=EvidenceLocator(type="docx_textbox", data={"textbox_index": index}),
        acl_scopes=context.acl_scopes,
        metadata=metadata,
    )


def _header_footer_unit(
    part_name: str, text: str, context: _DocxContext, *, ordinal: int
) -> ContentUnit | None:
    normalized = normalize_text(text)
    if not normalized:
        return None
    part_type = "header" if "header" in part_name else "footer"
    return ContentUnit(
        id=stable_id("unit", context.extraction_id, str(ordinal)),
        extraction_id=context.extraction_id,
        document_id=context.document_id,
        artifact_id=context.artifact_id,
        ordinal=ordinal,
        unit_type="docx_header_footer",
        title=f"{context.path.name} - {part_name}",
        body=text,
        body_normalized=normalized,
        lexical_text=normalized,
        locator=EvidenceLocator(type="docx_header_footer", data={"part": part_name}),
        acl_scopes=context.acl_scopes,
        metadata={"part_type": part_type},
    )


def _note_unit(
    kind: str, note_id: str, text: str, context: _DocxContext, *, ordinal: int
) -> ContentUnit | None:
    normalized = normalize_text(text)
    if not normalized:
        return None
    unit_type = f"docx_{kind}"
    return ContentUnit(
        id=stable_id("unit", context.extraction_id, str(ordinal)),
        extraction_id=context.extraction_id,
        document_id=context.document_id,
        artifact_id=context.artifact_id,
        ordinal=ordinal,
        unit_type=unit_type,
        title=f"{context.path.name} - {kind} {note_id}",
        body=text,
        body_normalized=normalized,
        lexical_text=normalized,
        locator=EvidenceLocator(type=unit_type, data={"note_id": note_id}),
        acl_scopes=context.acl_scopes,
        metadata={},
    )


# --- quality scoring ---------------------------------------------------------------------------


def _compute_quality(paragraphs: list[_ParagraphRecord], aggregate_text: str) -> float:
    # Blank paragraphs are ordinary formatting (spacing between sections,
    # a template's unused lines), not an extraction failure. Scoring on
    # non_empty/total paragraph density (the previous formula) unfairly
    # tanks quality for a legitimately blank-paragraph-heavy document - one
    # real paragraph plus 50 blank ones scored ~0.018 even though the real
    # paragraph extracted perfectly. This parser has no per-paragraph
    # extraction-failure signal (unlike pptx's failed-part tracking or the
    # header/footer PARTIAL_PARSE warnings this parser already has), so the
    # only content-derived signal available here is binary: did any
    # non-empty paragraph extract when the document actually has
    # paragraphs at all. A document with zero body paragraphs (all content
    # in tables/textboxes/header-footer) still earns full credit for this
    # factor instead of being zeroed out.
    total = len(paragraphs)
    non_empty = sum(1 for record in paragraphs if record.text.strip())
    extracted_content = 1.0 if (total == 0 or non_empty > 0) else 0.0
    replacement_ratio = (
        aggregate_text.count(_REPLACEMENT_CHAR) / len(aggregate_text) if aggregate_text else 0.0
    )
    quality = 0.95 * extracted_content * (1 - min(replacement_ratio, 1.0))
    return round(max(0.0, min(1.0, quality)), 4)
