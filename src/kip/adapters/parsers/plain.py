from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Final, Literal

from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.domain.text import normalize_text
from kip.ids import new_id, sha256_bytes, stable_id

# Byte-order marks stripped before decoding. Only the marker bytes are
# removed; KIP does not decode as UTF-16 (see decode_text_bytes) - stripping
# a stray UTF-16 BOM just keeps it from leaking into the decoded text as
# mojibake ahead of the UTF-8/CP949 attempts below.
_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_LE_BOM = b"\xff\xfe"
_UTF16_BE_BOM = b"\xfe\xff"
_BOMS: Final = (_UTF8_BOM, _UTF16_LE_BOM, _UTF16_BE_BOM)

# Above this replacement-character ratio, a fallback decode is flagged as
# uncertain instead of silently reported as full quality.
ENCODING_UNCERTAIN_FLOOR: Final = 0.05
_REPLACEMENT_CHAR = "�"


@dataclass
class DecodedText:
    """Result of the shared plain-text/CSV encoding ladder.

    Callers (PlainTextParser, CsvTableParser) merge `status`/`quality`/
    `warnings` straight into their ExtractionRun instead of deriving their
    own encoding-quality signal.
    """

    text: str
    encoding: str
    status: Literal["succeeded", "partial"]
    quality: float
    warnings: list[str] = field(default_factory=list)


def _strip_bom(raw: bytes) -> bytes:
    for bom in _BOMS:
        if raw.startswith(bom):
            return raw[len(bom) :]
    return raw


def decode_text_bytes(raw: bytes) -> DecodedText:
    """Decode raw bytes with a small, deterministic encoding ladder.

    Order: UTF-8 strict (after BOM stripping) -> CP949 strict -> UTF-8 with
    errors="replace". Intentionally limited to the two encodings KIP's NAS
    sources actually produce, plus a bounded-quality fallback - no chardet
    dependency.
    """
    stripped = _strip_bom(raw)
    try:
        text = stripped.decode("utf-8")
        return DecodedText(text=text, encoding="utf-8", status="succeeded", quality=1.0)
    except UnicodeDecodeError:
        pass
    try:
        text = stripped.decode("cp949")
        return DecodedText(
            text=text,
            encoding="cp949",
            status="succeeded",
            quality=1.0,
            warnings=["decoded as cp949"],
        )
    except UnicodeDecodeError:
        pass
    text = stripped.decode("utf-8", errors="replace")
    total_chars = len(text)
    replacement_ratio = (text.count(_REPLACEMENT_CHAR) / total_chars) if total_chars else 0.0
    quality = round(1.0 * (1 - replacement_ratio), 4)
    if replacement_ratio > ENCODING_UNCERTAIN_FLOOR:
        return DecodedText(
            text=text,
            encoding="utf-8",
            status="partial",
            quality=quality,
            warnings=[
                "ENCODING_UNCERTAIN: "
                f"{replacement_ratio:.0%} replacement characters after utf-8/cp949 attempts"
            ],
        )
    return DecodedText(text=text, encoding="utf-8", status="succeeded", quality=quality)


class PlainTextParser:
    name = "plain-text"
    version = "1.0"
    extensions: ClassVar[set[str]] = {
        ".txt",
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".log",
    }

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self.extensions

    def parse(self, path: Path, *, artifact_id: str, document_id: str, acl_scopes: list[str]) -> tuple[ExtractionRun, list[ContentUnit]]:
        raw = path.read_bytes()
        decoded = decode_text_bytes(raw)
        text = decoded.text
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
            metadata={
                "media_type": mimetypes.guess_type(path.name)[0] or "text/plain",
                "encoding": decoded.encoding,
            },
        )
        extraction = ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name=self.name,
            parser_version=self.version,
            status=decoded.status,
            quality_score=decoded.quality,
            output_hash=sha256_bytes(text.encode("utf-8")),
            warnings=decoded.warnings,
            metadata={"encoding": decoded.encoding},
        )
        return extraction, [unit]
