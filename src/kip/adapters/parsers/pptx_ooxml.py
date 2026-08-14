from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final
from xml.etree import ElementTree as ET

from kip.domain.json_types import JsonObject
from kip.errors import ValidationError

_REL_NS: Final = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS: Final = "http://schemas.openxmlformats.org/package/2006/relationships"
_PML_NS: Final = "http://schemas.openxmlformats.org/presentationml/2006/main"
_MAX_ENTRIES: Final = 20_000
_MAX_UNCOMPRESSED: Final = 512 * 1024 * 1024
_MAX_RATIO: Final = 200


@dataclass(frozen=True, slots=True)
class PptxComment:
    slide: int
    index: int
    author: str
    text: str
    created_at: str | None


@dataclass(frozen=True, slots=True)
class PptxDiagram:
    slide: int
    part: str
    text: str


@dataclass(frozen=True, slots=True)
class PptxPackageInfo:
    hidden_slides: frozenset[int]
    comments: tuple[PptxComment, ...]
    diagrams: tuple[PptxDiagram, ...]
    warnings: tuple[str, ...]
    animation_slide_count: int
    transition_slide_count: int
    embedded_object_count: int
    media_object_count: int
    external_relationship_count: int
    document_properties: JsonObject


@dataclass(frozen=True, slots=True)
class _AuxiliaryPartContext:
    archive: zipfile.ZipFile
    part: str
    slide: int
    warnings: list[str]


def scan_pptx_package(path: Path) -> PptxPackageInfo:
    with zipfile.ZipFile(path) as archive:
        _check_archive(archive)
        slide_paths = _slide_paths(archive)
        authors = _comment_authors(archive)
        hidden: set[int] = set()
        comments: list[PptxComment] = []
        diagrams: list[PptxDiagram] = []
        warnings: list[str] = []
        animations = 0
        transitions = 0
        embedded = 0
        media_parts: set[str] = set()
        external = 0
        for slide_number, slide_path in enumerate(slide_paths, start=1):
            try:
                root = ET.fromstring(archive.read(slide_path))
            except (KeyError, ET.ParseError) as exc:
                warnings.append(f"PARTIAL_PARSE slide {slide_number}: {exc}")
                continue
            if root.attrib.get("show", "1").lower() in {"0", "false", "off"}:
                hidden.add(slide_number)
            animations += int(_has_local(root, "timing"))
            transitions += int(_has_local(root, "transition"))
            for relationship in _relationships(archive, slide_path):
                target_mode = relationship.attrib.get("TargetMode", "Internal")
                if target_mode == "External":
                    external += 1
                    continue
                target = relationship.attrib.get("Target")
                relation_type = relationship.attrib.get("Type", "")
                if not target:
                    continue
                part = _resolve_target(slide_path, target)
                part_context = _AuxiliaryPartContext(
                    archive=archive,
                    part=part,
                    slide=slide_number,
                    warnings=warnings,
                )
                if relation_type.endswith("/comments"):
                    comments.extend(_comments(part_context, authors))
                elif relation_type.endswith("/diagramData"):
                    diagram = _diagram(part_context)
                    if diagram is not None:
                        diagrams.append(diagram)
                elif relation_type.endswith(("/oleObject", "/package")):
                    embedded += 1
                elif relation_type.endswith(("/audio", "/video", "/media")):
                    media_parts.add(part)
        return PptxPackageInfo(
            hidden_slides=frozenset(hidden),
            comments=tuple(comments),
            diagrams=tuple(diagrams),
            warnings=tuple(warnings),
            animation_slide_count=animations,
            transition_slide_count=transitions,
            embedded_object_count=embedded,
            media_object_count=len(media_parts),
            external_relationship_count=external,
            document_properties=_document_properties(archive),
        )


def _check_archive(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > _MAX_ENTRIES:
        raise ValidationError("PPTX has too many ZIP entries")
    uncompressed = sum(info.file_size for info in infos)
    compressed = max(1, sum(info.compress_size for info in infos))
    if uncompressed > _MAX_UNCOMPRESSED or uncompressed / compressed > _MAX_RATIO:
        raise ValidationError("PPTX decompression limits exceeded")


def _slide_paths(archive: zipfile.ZipFile) -> list[str]:
    presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
    relationships = {
        item.attrib.get("Id", ""): item.attrib.get("Target", "")
        for item in _relationships(archive, "ppt/presentation.xml")
    }
    paths: list[str] = []
    for slide_id in presentation.findall(f".//{{{_PML_NS}}}sldId"):
        relation_id = slide_id.attrib.get(f"{{{_REL_NS}}}id", "")
        target = relationships.get(relation_id)
        if target:
            paths.append(_resolve_target("ppt/presentation.xml", target))
    return paths


def _document_properties(archive: zipfile.ZipFile) -> JsonObject:
    part = "docProps/core.xml"
    if part not in archive.namelist():
        return {}
    try:
        root = ET.fromstring(archive.read(part))
    except ET.ParseError:
        return {}
    names = {
        "title": "title",
        "creator": "author",
        "subject": "subject",
        "keywords": "keywords",
        "category": "category",
        "created": "created",
        "modified": "modified",
    }
    properties: JsonObject = {}
    for element in root:
        output_name = names.get(element.tag.rsplit("}", 1)[-1])
        value = (element.text or "").strip()
        if output_name and value:
            properties[output_name] = (
                _timestamp(value) if output_name in {"created", "modified"} else value
            )
    return properties


def _timestamp(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return value


def _relationships(archive: zipfile.ZipFile, part: str) -> list[ET.Element]:
    path = PurePosixPath(part)
    rels_path = str(path.parent / "_rels" / f"{path.name}.rels")
    if rels_path not in archive.namelist():
        return []
    root = ET.fromstring(archive.read(rels_path))
    return root.findall(f"{{{_PACKAGE_REL_NS}}}Relationship")


def _comment_authors(archive: zipfile.ZipFile) -> dict[str, str]:
    names = [name for name in archive.namelist() if name.endswith("commentAuthors.xml")]
    if not names:
        return {}
    root = ET.fromstring(archive.read(names[0]))
    return {
        item.attrib.get("id", ""): item.attrib.get("name", "")
        for item in root.findall(f".//{{{_PML_NS}}}cmAuthor")
    }


def _comments(
    context: _AuxiliaryPartContext,
    authors: dict[str, str],
) -> list[PptxComment]:
    try:
        root = ET.fromstring(context.archive.read(context.part))
    except (KeyError, ET.ParseError) as exc:
        context.warnings.append(f"PARTIAL_PARSE slide {context.slide} comments: {exc}")
        return []
    comments: list[PptxComment] = []
    for item in root.findall(f".//{{{_PML_NS}}}cm"):
        text = "".join(item.itertext()).strip()
        if not text:
            continue
        comments.append(
            PptxComment(
                slide=context.slide,
                index=_integer(item.attrib.get("idx")),
                author=authors.get(item.attrib.get("authorId", ""), ""),
                text=text,
                created_at=item.attrib.get("dt"),
            )
        )
    return comments


def _diagram(
    context: _AuxiliaryPartContext,
) -> PptxDiagram | None:
    try:
        root = ET.fromstring(context.archive.read(context.part))
    except (KeyError, ET.ParseError) as exc:
        context.warnings.append(f"PARTIAL_PARSE slide {context.slide} diagram: {exc}")
        return None
    text = "\n".join(
        value
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "t" and (value := (element.text or "").strip())
    )
    return PptxDiagram(slide=context.slide, part=context.part, text=text) if text else None


def _resolve_target(source_part: str, target: str) -> str:
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))
    return resolved.lstrip("/")


def _integer(value: str | None) -> int:
    return int(value) if value and value.isdigit() else 0


def _has_local(root: ET.Element, name: str) -> bool:
    return any(element.tag.rsplit("}", 1)[-1] == name for element in root.iter())
