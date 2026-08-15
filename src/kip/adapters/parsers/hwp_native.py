from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree as ET

from kip.adapters.parsers.text_quality import hwp_text_quality
from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.domain.text import normalize_text
from kip.errors import DependencyUnavailableError, ParserError
from kip.ids import new_id, sha256_bytes, stable_id

_HWP_OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_HWPX_REQUIRED_ENTRIES = frozenset({"mimetype", "Contents/content.hpf"})
_HWPX_SECTION_FILE_RE = re.compile(r"section(\d+)\.xml$")


@dataclass(frozen=True, slots=True)
class _SectionSpan:
    """A verified ``[start, end)`` char range of the library's own
    ``extract_text()`` output, attributed to one real section index."""

    section: int
    start: int
    end: int


def _reconstruct_section_spans(reader: Any, path: Path, full_text: str) -> list[_SectionSpan] | None:
    """Recompute per-section text via the pinned ``hwp-hwpx-parser``
    package's own per-section internals, so each evidence unit can carry a
    real section index instead of always ``None``.

    ``hwp-hwpx-parser`` has no public per-section API: ``HWPXReader`` only
    exposes section text via the private ``_get_section_files()`` /
    ``_extract_section()`` pair, and ``HWP5Reader`` only via
    ``_iter_sections()`` / ``_read_section()`` / ``_extract_section_text()``.
    Both pairs are exactly what the package's own public ``extract_text()``
    calls internally (see ``hwp_hwpx_parser/{hwpx,hwp5}.py`` in the pinned
    ``hwp-hwpx-parser>=1,<2`` dependency), so replaying them here reproduces
    identical per-section text as long as that internal shape has not
    drifted. This function is the ONE place in this codebase that reaches
    into those private internals, so a future upgrade that renames or
    restructures them breaks in exactly one place: the runtime verification
    below (the reconstructed concatenation must equal
    ``reader.extract_text()``) then makes the caller fall back to
    ``locator.data["section"] = None`` instead of ever emitting a wrong
    section number, and the extracted text itself never changes either way.

    Iteration deliberately stays in the library's own file/stream order
    (``HWPXReader._get_section_files()`` sorts *lexically* - e.g.
    ``section10.xml`` before ``section2.xml``) rather than being re-sorted
    here, because ``full_text`` was itself built by the library walking
    sections in that same order, and text output must stay byte-identical.
    What IS computed numerically - never trusted from lexical/iteration
    position - is the section *label* itself, via ``int()`` on the captured
    digits (HWPX) or the real ``BodyText/SectionN`` OLE stream index
    (HWP5, whose own ``_iter_sections()`` is already numeric). So a chunk
    physically inside ``section10.xml`` is correctly labelled section 10,
    never mislabeled by its position among the other section files.
    """
    try:
        module = import_module("hwp_hwpx_parser")
        options = module.ExtractOptions()
        backend = reader._get_reader()  # pinned dependency's private accessor
        suffix = path.suffix.lower()
        numbered: list[tuple[int, str]] = []
        if suffix == ".hwpx":
            # Mirrors HWPXReader.extract_text()'s own preamble exactly, so
            # image markers / memo handling inside _extract_section behave
            # identically to the first (already-completed) extract_text()
            # call rather than skipping state extract_text() would set up.
            backend._current_options = options
            backend._reset_counters()
            backend._load_memo_properties()
            for section_file in backend._get_section_files():
                match = _HWPX_SECTION_FILE_RE.search(section_file)
                if match is None:
                    return None
                section_text = backend._extract_section(section_file, options)
                if section_text.strip():
                    numbered.append((int(match.group(1)), section_text))
        elif suffix == ".hwp":
            backend._reset_counters()
            for section_index in backend._iter_sections():
                section_data = backend._read_section(section_index)
                section_text = backend._extract_section_text(section_data, options)
                if section_text.strip():
                    numbered.append((int(section_index), section_text))
        else:
            return None
    except Exception:
        return None

    separator = str(options.paragraph_separator)
    reconstructed = separator.join(text for _, text in numbered)
    if reconstructed != full_text:
        return None

    spans: list[_SectionSpan] = []
    offset = 0
    for section_number, section_text in numbered:
        start = offset
        end = start + len(section_text)
        spans.append(_SectionSpan(section=section_number, start=start, end=end))
        offset = end + len(separator)
    return spans


def _section_for_offset(spans: list[_SectionSpan], offset: int) -> int | None:
    for span in spans:
        if span.start <= offset < span.end:
            return span.section
    for span in spans:
        if offset < span.start:
            return span.section
    return spans[-1].section if spans else None


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


def split_text_spans(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int = 0,
) -> list[tuple[int, str]]:
    """Split into (offset, chunk) windows with an overlapping tail.

    Overlap keeps a fact that straddles a window boundary fully inside at
    least one chunk, so it stays retrievable by lexical and vector search.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if overlap_chars < 0 or overlap_chars >= max_chars // 2:
        raise ValueError("overlap_chars must be non-negative and below half of max_chars")
    if len(text) <= max_chars:
        return [(0, text)] if text else []

    spans: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            line_break = text.rfind("\n", start, end)
            if line_break > start + max_chars // 2:
                end = line_break + 1
        spans.append((start, text[start:end]))
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return spans


def split_text(text: str, *, max_chars: int, overlap_chars: int = 0) -> list[str]:
    return [
        chunk
        for _, chunk in split_text_spans(
            text,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
    ]


class HwpNativeParser:
    name = "hwp-hwpx-parser"
    version = "1.1"

    def __init__(
        self,
        *,
        max_chars_per_unit: int = 4000,
        overlap_chars: int = 400,
    ) -> None:
        self.max_chars_per_unit = max_chars_per_unit
        # Overlap must stay under half the window for splitting to progress;
        # clamp so small windows (tests, tuned configs) remain valid.
        self.overlap_chars = min(
            overlap_chars,
            max(0, max_chars_per_unit // 2 - 1),
        )

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
        # A missing section label is not content loss: the unit text comes from
        # extract_text() either way, so this warning must not downgrade the
        # extraction to "partial" (which operators read as "some content is
        # missing"). Counted separately from warnings that do signal degradation.
        enrichment_only_warnings = 0
        try:
            with Reader(path) as reader:
                text = reader.extract_text() or ""
                section_spans = _reconstruct_section_spans(reader, path, text)
                if section_spans is None:
                    warnings.append(
                        "SECTION_INDEX_UNAVAILABLE: per-section reconstruction did not "
                        "verify against extract_text() output; section left as None"
                    )
                    enrichment_only_warnings += 1
                try:
                    table_count = len(reader.tables)
                except (OSError, RuntimeError, ValueError, ET.ParseError) as exc:
                    table_count = 0
                    warnings.append(f"table metadata unavailable: {exc}")
                try:
                    image_count = len(reader.get_images())
                except (OSError, RuntimeError, ValueError, ET.ParseError) as exc:
                    image_count = 0
                    warnings.append(f"image metadata unavailable: {exc}")
        except (OSError, RuntimeError, ValueError, EOFError, KeyError, ET.ParseError) as exc:
            raise ParserError(f"native HWP parse failed: {path}: {exc}") from exc

        if not text.strip():
            raise ParserError("native HWP parser returned no usable text")

        extraction_id = new_id("ext")
        spans = split_text_spans(
            text,
            max_chars=self.max_chars_per_unit,
            overlap_chars=self.overlap_chars,
        )
        units: list[ContentUnit] = []
        for ordinal, (chunk_start, chunk) in enumerate(spans):
            normalized = normalize_text(chunk)
            if not normalized:
                continue
            end = chunk_start + len(chunk)
            section = (
                _section_for_offset(section_spans, chunk_start)
                if section_spans is not None
                else None
            )
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
                            "section": section,
                            "chunk": ordinal,
                            "char_start": chunk_start,
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

        if not units:
            raise ParserError("native HWP parser returned no usable text units")

        degraded_warnings = len(warnings) - enrichment_only_warnings
        status: Literal["succeeded", "partial"] = (
            "partial" if degraded_warnings else "succeeded"
        )
        extraction = ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name=self.name,
            parser_version=self.version,
            status=status,
            quality_score=hwp_text_quality(text, warning_count=len(warnings)),
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
