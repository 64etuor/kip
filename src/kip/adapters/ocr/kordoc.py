from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from kip.adapters.parsers.structured_blocks import render_external_block
from kip.domain.json_types import JsonObject, JsonValue
from kip.errors import ParserError
from kip.ports.ocr import OcrBlock, OcrDocument

_JSON_OBJECT: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class KordocOcrConfig:
    argv: tuple[str, ...]
    version_argv: tuple[str, ...] = ()
    expected_version: str = "4.7.3"
    timeout_seconds: int = 120


@dataclass(frozen=True, slots=True)
class KordocVersionProbe:
    """Result of resolving and version-checking the configured Kordoc runtime.

    ``ok`` is True only when the configured ``version_argv`` command ran,
    exited zero, and reported exactly ``expected_version``. ``version`` is the
    detected version string when one could be parsed (even on failure, so a
    mismatch can be reported). ``error`` is an actionable, human-readable
    reason set whenever ``ok`` is False.
    """

    ok: bool
    version: str | None
    error: str | None


def probe_kordoc_version(config: KordocOcrConfig) -> KordocVersionProbe:
    """Resolve the Kordoc runtime configured by ``config`` without raising.

    Runs the same ``version_argv`` command that :class:`KordocOcrAdapter`
    requires before every ``recognize`` call, so this is the single place
    that decides whether a configured Kordoc runtime is usable. Both the
    adapter's pre-flight check (which raises) and ``kip doctor`` (which
    reports) call this helper so the resolution policy is defined once.
    """
    if not config.version_argv:
        return KordocVersionProbe(ok=True, version=None, error=None)
    try:
        completed = subprocess.run(
            config.version_argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return KordocVersionProbe(
            ok=False, version=None, error=f"Kordoc version check failed: {exc}"
        )
    actual = completed.stdout.strip().removeprefix("v")
    if completed.returncode != 0 or actual != config.expected_version:
        return KordocVersionProbe(
            ok=False,
            version=actual or None,
            error=(
                f"Kordoc OCR expected {config.expected_version}, "
                f"found {actual or 'unknown'}"
            ),
        )
    return KordocVersionProbe(ok=True, version=actual, error=None)


class KordocOcrAdapter:
    name = "kordoc-ppocrv5-korean"
    version = "4.7.3"

    def __init__(self, config: KordocOcrConfig) -> None:
        self._config = config

    def recognize(self, paths: tuple[Path, ...]) -> tuple[OcrDocument, ...]:
        if not paths:
            return ()
        self._require_version()
        try:
            completed = subprocess.run(
                [*self._config.argv, *(str(path) for path in paths)],
                check=False,
                capture_output=True,
                text=True,
                timeout=self._config.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ParserError(f"Kordoc OCR command failed: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr[-2000:].replace("\x00", " ")
            raise ParserError(f"Kordoc OCR exited {completed.returncode}: {detail}")
        payloads = _decode_json_stream(completed.stdout)
        if len(payloads) != len(paths):
            raise ParserError(
                f"Kordoc OCR returned {len(payloads)} documents for {len(paths)} inputs"
            )
        return tuple(
            _document(path, payload)
            for path, payload in zip(paths, payloads, strict=True)
        )

    def _require_version(self) -> None:
        probe = probe_kordoc_version(self._config)
        if not probe.ok:
            assert probe.error is not None
            raise ParserError(probe.error)


def _decode_json_stream(value: str) -> tuple[JsonObject, ...]:
    decoder = json.JSONDecoder()
    offset = 0
    payloads: list[JsonObject] = []
    try:
        while offset < len(value):
            while offset < len(value) and value[offset].isspace():
                offset += 1
            if offset >= len(value):
                break
            decoded, offset = decoder.raw_decode(value, offset)
            payloads.append(_JSON_OBJECT.validate_python(decoded))
    except (json.JSONDecodeError, PydanticValidationError) as exc:
        raise ParserError(f"Kordoc OCR returned invalid JSON: {exc}") from exc
    return tuple(payloads)


def _document(path: Path, payload: JsonObject) -> OcrDocument:
    if payload.get("success") is not True:
        message = payload.get("error")
        raise ParserError(f"Kordoc OCR rejected {path.name}: {message or 'unknown error'}")
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list):
        raise ParserError(f"Kordoc OCR returned no blocks for {path.name}")
    blocks = tuple(
        block
        for item in raw_blocks
        if (block := _block(item)).block_type != "image" and block.text.strip()
    )
    metadata = _json_object(payload.get("metadata")) or {}
    warnings = _warnings(payload.get("warnings"))
    return OcrDocument(
        source_path=path,
        blocks=blocks,
        metadata=metadata,
        warnings=warnings,
    )


def _block(value: JsonValue) -> OcrBlock:
    block = _json_object(value)
    if block is None:
        raise ParserError("Kordoc OCR block is not an object")
    rendered = render_external_block(block)
    page_value = block.get("pageNumber")
    page = (
        page_value
        if isinstance(page_value, int) and not isinstance(page_value, bool)
        else None
    )
    metadata = dict(block)
    for key in ("text", "type", "pageNumber", "bbox"):
        metadata.pop(key, None)
    metadata.update(rendered.metadata)
    return OcrBlock(
        text=rendered.body,
        block_type=str(block.get("type") or "paragraph"),
        page=page,
        bbox=_json_object(block.get("bbox")),
        metadata=metadata,
    )


def _json_object(value: JsonValue | None) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def _warnings(value: JsonValue | None) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    warnings: list[str] = []
    for item in value:
        if isinstance(item, str):
            warnings.append(item)
            continue
        warning = _json_object(item)
        if warning is None:
            continue
        code = warning.get("code")
        message = warning.get("message")
        page = warning.get("page")
        code_text = code if isinstance(code, str) else "OCR_WARNING"
        if code_text == "OCR_APPLIED":
            continue
        message_text = message if isinstance(message, str) else "OCR warning"
        page_text = (
            f" page {page}"
            if isinstance(page, int) and not isinstance(page, bool)
            else ""
        )
        warnings.append(f"{code_text}{page_text}: {message_text}")
    return tuple(warnings)
