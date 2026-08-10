from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import ClassVar

from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.domain.text import normalize_text
from kip.ids import new_id, sha256_bytes, stable_id


class PlainTextParser:
    name = "plain-text"
    version = "1.0"
    extensions: ClassVar[set[str]] = {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".log",
    }

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.extensions

    def parse(self, path: Path, *, artifact_id: str, document_id: str, acl_scopes: list[str]) -> tuple[ExtractionRun, list[ContentUnit]]:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        normalized = normalize_text(text)
        extraction_id = new_id("ext")
        unit = ContentUnit(
            id=stable_id("unit", extraction_id, "0"),
            extraction_id=extraction_id,
            document_id=document_id,
            artifact_id=artifact_id,
            ordinal=0,
            unit_type="text_document",
            title=path.name,
            body=text,
            body_normalized=normalized,
            lexical_text=normalized,
            locator=EvidenceLocator(type="text_line_range", data={"start_line": 1, "end_line": max(1, text.count("\n") + 1)}),
            acl_scopes=acl_scopes,
            metadata={"media_type": mimetypes.guess_type(path.name)[0] or "text/plain"},
        )
        extraction = ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name=self.name,
            parser_version=self.version,
            status="succeeded",
            quality_score=1.0,
            output_hash=sha256_bytes(text.encode("utf-8")),
        )
        return extraction, [unit]
