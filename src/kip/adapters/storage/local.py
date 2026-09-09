from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter

from kip.adapters.parsers.xlsx_read import read_xlsx_range
from kip.adapters.storage.cloud_files import is_cloud_placeholder
from kip.domain.source_access import FilesystemAccessPolicy
from kip.domain.xlsx import XlsxCell
from kip.errors import NotFoundError
from kip.ids import sha256_bytes

_CELL_MATRIX: Final = TypeAdapter(list[list[XlsxCell]])


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
    source_policy: FilesystemAccessPolicy | None = None

    def _path(self, path: Path) -> Path:
        if self.source_policy is not None:
            path = self.source_policy.require_path(path)
        return path

    def sha256(self, path: Path) -> str | None:
        path = self._path(path)
        if not path.exists() or not path.is_file():
            return None
        if is_cloud_placeholder(path.stat()):
            return None
        return _sha256_file(path)

    def stat(self, path: Path) -> tuple[int, int] | None:
        path = self._path(path)
        try:
            info = path.stat()
        except OSError:
            return None
        if not path.is_file():
            return None
        if is_cloud_placeholder(info):
            return None
        return (info.st_size, info.st_mtime_ns)

    def require_sha256(self, path: Path) -> str:
        value = self.sha256(path)
        if value is None:
            raise NotFoundError(f"source file is unavailable: {path}")
        return value


_SUPPORTED_WORKBOOK_EXTENSIONS: Final = {".xlsx", ".xlsm"}


@dataclass(frozen=True, slots=True)
class LocalWorkbookReader:
    def read(self, path: Path, sheet: str, cell_range: str) -> list[list[XlsxCell]]:
        result = read_xlsx_range(path, sheet, cell_range)
        return _CELL_MATRIX.validate_python(result["cells"])

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in _SUPPORTED_WORKBOOK_EXTENSIONS
