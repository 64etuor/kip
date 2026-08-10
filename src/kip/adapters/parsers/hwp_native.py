from __future__ import annotations

import zipfile
from importlib import import_module
from pathlib import Path
from typing import Any, Literal

from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.domain.text import normalize_text
from kip.errors import DependencyUnavailableError, ParserError
from kip.ids import new_id, sha256_bytes, stable_id

_HWP_OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_HWPX_REQUIRED_ENTRIES = frozenset({"mimetype", "Contents/content.hpf"})


def has_hwp_signature(path: Path) -> bool:
    suffix = path.suffix.lower()
    try:
        if suffix == ".hwp":
            with path.open("rb") as handle:
                return handle.read(len(_HWP_OLE_SIGNATURE)) == _HWP_OLE_SIGNATURE
        if suffix == ".hwpx":
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                return _HWPX_REQUIRED_ENTRIES.issubset(names)
    except (OSError, zipfile.BadZipFile):
        return False
    return False


def split_text(text: str, *, max_chars: int) -> list[str]:
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


class HwpNativeParser:
    name = "hwp-hwpx-parser"
    version = "1.0"

    def __init__(self, *, max_chars_per_unit: int = 4000) -> None:
        self.max_chars_per_unit = max_chars_per_unit

    def supports(self, path: Path) -> bool:
        return has_hwp_signature(path)

    def parse(
        self,
        path: Path,
        *,
        artifact_id: str,
        document_id: str,
        acl_scopes: list[str],
    ) -> tuple[ExtractionRun, list[ContentUnit]]:
        try:
            Reader = import_module("hwp_hwpx_parser").Reader
        except ImportError as exc:
            raise DependencyUnavailableError(
                "Install hwp-hwpx-parser for native HWP/HWPX parsing"
            ) from exc

        warnings: list[str] = []
        try:
            with Reader(path) as reader:
                text = reader.extract_text() or ""
                try:
                    table_count = len(reader.tables)
                except (OSError, RuntimeError, ValueError) as exc:
                    table_count = 0
                    warnings.append(f"table metadata unavailable: {exc}")
                try:
                    image_count = len(reader.get_images())
                except (OSError, RuntimeError, ValueError) as exc:
                    image_count = 0
                    warnings.append(f"image metadata unavailable: {exc}")
        except (OSError, RuntimeError, ValueError, EOFError, KeyError) as exc:
            raise ParserError(f"native HWP parse failed: {path}: {exc}") from exc

        if not text.strip():
            raise ParserError("native HWP parser returned no usable text")

        extraction_id = new_id("ext")
        chunks = split_text(text, max_chars=self.max_chars_per_unit)
        units: list[ContentUnit] = []
        cursor = 0
        for ordinal, chunk in enumerate(chunks):
            normalized = normalize_text(chunk)
            if not normalized:
                cursor += len(chunk)
                continue
            end = cursor + len(chunk)
            units.append(
                ContentUnit(
                    id=stable_id("unit", extraction_id, str(ordinal)),
                    extraction_id=extraction_id,
                    document_id=document_id,
                    artifact_id=artifact_id,
                    ordinal=ordinal,
                    unit_type="hwp_native_chunk",
                    title=path.name,
                    body=chunk,
                    body_normalized=normalized,
                    lexical_text=normalized,
                    locator=EvidenceLocator(
                        type="hwp_structure",
                        data={
                            "section": None,
                            "chunk": ordinal,
                            "char_start": cursor,
                            "char_end": end,
                            "format": path.suffix.lower(),
                        },
                    ),
                    acl_scopes=acl_scopes,
                    metadata={
                        "table_count": table_count,
                        "image_count": image_count,
                        "full_text_length": len(text),
                    },
                )
            )
            cursor = end

        if not units:
            raise ParserError("native HWP parser returned no usable text units")

        status: Literal["succeeded", "partial"] = "partial" if warnings else "succeeded"
        extraction = ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name=self.name,
            parser_version=self.version,
            status=status,
            quality_score=0.95 if status == "succeeded" else 0.80,
            output_hash=sha256_bytes(text.encode("utf-8")),
            warnings=warnings,
            metadata={
                "format": path.suffix.lower(),
                "table_count": table_count,
                "image_count": image_count,
                "full_text_length": len(text),
                "chunk_count": len(units),
            },
        )
        return extraction, units


class HwpParserChain:
    name = "hwp-broker"
    version = "2.0"

    def __init__(self, native: HwpNativeParser | None, fallback: Any) -> None:
        self.native = native
        self.fallback = fallback
        self.version = "2.0-native-primary" if native is not None else "2.0"

    def supports(self, path: Path) -> bool:
        return has_hwp_signature(path)

    def parse(
        self,
        path: Path,
        *,
        artifact_id: str,
        document_id: str,
        acl_scopes: list[str],
    ) -> tuple[ExtractionRun, list[ContentUnit]]:
        failures: list[str] = []
        if self.native is not None:
            try:
                return self.native.parse(
                    path,
                    artifact_id=artifact_id,
                    document_id=document_id,
                    acl_scopes=acl_scopes,
                )
            except (DependencyUnavailableError, ParserError, OSError, RuntimeError, ValueError) as exc:
                failures.append(f"{self.native.name}: {exc}")

        try:
            extraction, units = self.fallback.parse(
                path,
                artifact_id=artifact_id,
                document_id=document_id,
                acl_scopes=acl_scopes,
            )
        except ParserError as exc:
            failures.append(str(exc))
            raise ParserError("all HWP parsers failed: " + "; ".join(failures)) from exc

        if failures:
            extraction.warnings.extend(failures)
            extraction.metadata["native_primary_failed"] = True
        return extraction, units
