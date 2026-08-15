from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Final, Literal

from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.domain.text import normalize_text
from kip.errors import ParserError
from kip.ids import new_id, sha256_bytes, stable_id

# Simple, defense-in-depth backstop: XlsxShallowParser already guards its
# ZIP-container input via check_zip_bomb_guard, but plain text/CSV have no
# equivalent structural limit before this parser reads the whole file into
# memory with `read_bytes()`. This is deliberately not wired to
# `security.max_file_bytes` (that needs settings plumbing outside this
# parser's ownership) - just a hard cap matching the documented 500MB
# default so an unexpectedly huge file fails cleanly instead of exhausting
# memory.
_MAX_FILE_BYTES: Final = 500 * 1024 * 1024

# Byte-order marks stripped before decoding. Only the marker bytes are
# removed; the UTF-8/CP949 ladder below is still tried first and only falls
# back to UTF-16 recovery (see _decode_utf16_without_bom) when the primary
# attempt leaves embedded NUL bytes behind - stripping a stray UTF-16 BOM
# just keeps it from leaking into the decoded text as mojibake ahead of that.
_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_LE_BOM = b"\xff\xfe"
_UTF16_BE_BOM = b"\xfe\xff"
_BOMS: Final = (_UTF8_BOM, _UTF16_LE_BOM, _UTF16_BE_BOM)

# Above this replacement-character ratio, a fallback decode is flagged as
# uncertain instead of silently reported as full quality.
ENCODING_UNCERTAIN_FLOOR: Final = 0.05
_REPLACEMENT_CHAR = "�"
_NUL_CHAR = "\x00"


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


def _nul_ratio(text: str) -> float:
    return (text.count(_NUL_CHAR) / len(text)) if text else 0.0


def _decode_utf16_without_bom(raw: bytes) -> str | None:
    """Best-effort recovery for UTF-16 text saved without a byte-order mark.

    Plain ASCII interleaved with 0x00 bytes (the common shape of an
    ASCII/Latin string encoded as UTF-16) also happens to be valid UTF-8 -
    every byte is either an ASCII byte or a lone 0x00, both legal
    single-byte UTF-8 sequences - so the primary UTF-8 attempt silently
    "succeeds" with the interleaved NULs baked straight into the text. A BOM
    is required to know which UTF-16 byte order applies, so try both.
    Only accept a candidate that fully decodes and leaves no residual NULs
    of its own (a genuine UTF-16 recovery should not still contain NULs).
    """
    for encoding in ("utf-16-le", "utf-16-be"):
        try:
            candidate = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _NUL_CHAR not in candidate:
            return candidate
    return None


def _finalize(
    raw: bytes,
    text: str,
    *,
    encoding: str,
    status: Literal["succeeded", "partial"],
    quality: float,
    warnings: list[str],
) -> DecodedText:
    """Shared NUL safety net applied after every decode-ladder branch.

    A Postgres text column rejects an embedded NUL outright, and (unlike
    CsvTableParser, which already strips NUL before csv.reader for a
    different, structural reason) nothing here used to guard against one
    landing in `body`. Any embedded NUL surviving a decode - a UTF-16
    payload saved without a BOM, or a single stray corrupt byte - is always
    stripped from the emitted text and the result is flagged
    encoding-uncertain instead of silently reporting the ladder's original
    clean status/quality.
    """
    if _NUL_CHAR not in text:
        return DecodedText(text=text, encoding=encoding, status=status, quality=quality, warnings=warnings)
    recovered = _decode_utf16_without_bom(raw)
    if recovered is not None:
        return DecodedText(
            text=recovered,
            encoding="utf-16",
            status="partial",
            quality=0.6,
            warnings=[
                *warnings,
                "ENCODING_UNCERTAIN: NUL bytes indicate UTF-16 without a byte-order mark; "
                "recovered via UTF-16 decode",
            ],
        )
    nul_ratio = _nul_ratio(text)
    return DecodedText(
        text=text.replace(_NUL_CHAR, ""),
        encoding=encoding,
        status="partial",
        quality=round(quality * (1 - nul_ratio), 4),
        warnings=[
            *warnings,
            f"ENCODING_UNCERTAIN: {nul_ratio:.0%} embedded NUL characters after decode; NULs stripped",
        ],
    )


def decode_text_bytes(raw: bytes) -> DecodedText:
    """Decode raw bytes with a small, deterministic encoding ladder.

    Order: UTF-8 strict (after BOM stripping) -> CP949 strict -> UTF-8 with
    errors="replace". Intentionally limited to the two encodings KIP's NAS
    sources actually produce, plus a bounded-quality fallback - no chardet
    dependency. Every branch's result passes through `_finalize`, which
    guarantees the returned text never carries an embedded NUL byte.
    """
    stripped = _strip_bom(raw)
    try:
        text = stripped.decode("utf-8")
        return _finalize(stripped, text, encoding="utf-8", status="succeeded", quality=1.0, warnings=[])
    except UnicodeDecodeError:
        pass
    try:
        text = stripped.decode("cp949")
        return _finalize(
            stripped,
            text,
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
        return _finalize(
            stripped,
            text,
            encoding="utf-8",
            status="partial",
            quality=quality,
            warnings=[
                "ENCODING_UNCERTAIN: "
                f"{replacement_ratio:.0%} replacement characters after utf-8/cp949 attempts"
            ],
        )
    return _finalize(stripped, text, encoding="utf-8", status="succeeded", quality=quality, warnings=[])


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
        file_size = path.stat().st_size
        if file_size > _MAX_FILE_BYTES:
            raise ParserError(
                f"text file too large: {path}: {file_size} bytes exceeds the "
                f"{_MAX_FILE_BYTES} byte limit"
            )
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
