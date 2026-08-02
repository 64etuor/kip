from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from kip.application.analyzer import normalize_text
from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.errors import ParserError
from kip.ids import new_id, sha256_bytes, stable_id

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class DocxParser:
    name = "docx-xml"
    version = "1.0"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".docx"

    def parse(self, path: Path, *, artifact_id: str, document_id: str, acl_scopes: list[str]) -> tuple[ExtractionRun, list[ContentUnit]]:
        extraction_id = new_id("ext")
        try:
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
        except Exception as exc:
            raise ParserError(f"DOCX parse failed: {path}: {exc}") from exc
        paragraphs: list[str] = []
        for paragraph in root.findall(f".//{{{_W_NS}}}p"):
            text = "".join(node.text or "" for node in paragraph.iter(f"{{{_W_NS}}}t"))
            if text.strip():
                paragraphs.append(text)
        body = "\n".join(paragraphs)
        normalized = normalize_text(body)
        unit = ContentUnit(
            id=stable_id("unit", extraction_id, "0"),
            extraction_id=extraction_id,
            document_id=document_id,
            artifact_id=artifact_id,
            ordinal=0,
            unit_type="docx_document",
            title=path.name,
            body=body,
            body_normalized=normalized,
            lexical_text=normalized,
            locator=EvidenceLocator(type="document", data={"part": "word/document.xml"}),
            acl_scopes=acl_scopes,
        )
        extraction = ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name=self.name,
            parser_version=self.version,
            status="succeeded",
            quality_score=0.85,
            output_hash=sha256_bytes(body.encode("utf-8")),
        )
        return extraction, [unit]
