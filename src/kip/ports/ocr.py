from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from kip.domain.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class OcrBlock:
    text: str
    block_type: str
    page: int | None
    bbox: JsonObject | None
    metadata: JsonObject


@dataclass(frozen=True, slots=True)
class OcrDocument:
    source_path: Path
    blocks: tuple[OcrBlock, ...]
    metadata: JsonObject
    warnings: tuple[str, ...]


class OcrPort(Protocol):
    name: str
    version: str

    def recognize(self, paths: tuple[Path, ...]) -> tuple[OcrDocument, ...]: ...
