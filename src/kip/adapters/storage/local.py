from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter

from kip.adapters.parsers.xlsx_read import read_xlsx_range
from kip.domain.json_types import JsonObject
from kip.errors import NotFoundError
from kip.ids import sha256_bytes


_CELL_MATRIX: Final = TypeAdapter(list[list[JsonObject]])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class LocalContentAddressedStore:
    root: Path

    def put(self, data: bytes, *, suffix: str = "") -> str:
        digest = sha256_bytes(data)
        target = self.root / "sha256" / digest[:2] / f"{digest}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, target)
        return target.as_uri()


@dataclass(frozen=True, slots=True)
class LocalSourceFileInspector:
    def sha256(self, path: Path) -> str | None:
        if not path.exists() or not path.is_file():
            return None
        return _sha256_file(path)

    def require_sha256(self, path: Path) -> str:
        value = self.sha256(path)
        if value is None:
            raise NotFoundError(f"source file is unavailable: {path}")
        return value


@dataclass(frozen=True, slots=True)
class LocalWorkbookReader:
    def read(self, path: Path, sheet: str, cell_range: str) -> list[list[JsonObject]]:
        result = read_xlsx_range(path, sheet, cell_range)
        return _CELL_MATRIX.validate_python(result["cells"])
