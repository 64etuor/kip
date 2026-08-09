from __future__ import annotations

import fnmatch
import hashlib
import os
import time
from collections.abc import Iterator
from pathlib import Path

from kip.errors import SourceUnavailableError, ValidationError
from kip.ports.ingestion import DiscoveredFile

FileRecord = DiscoveredFile


class FileSystemConnector:
    name = "filesystem"
    kind = "filesystem"

    def __init__(
        self,
        root: Path,
        *,
        include_extensions: set[str] | None = None,
        exclude_globs: list[str] | None = None,
        settle_seconds: float = 2.0,
        follow_symlinks: bool = False,
        max_file_bytes: int = 500 * 1024 * 1024,
    ) -> None:
        self.root = root.resolve()
        self.include_extensions = {value.lower() for value in (include_extensions or set())}
        self.exclude_globs = exclude_globs or []
        self.settle_seconds = settle_seconds
        self.follow_symlinks = follow_symlinks
        self.max_file_bytes = max_file_bytes

    def scan(self) -> Iterator[FileRecord]:
        if not self.root.exists() or not self.root.is_dir():
            raise SourceUnavailableError(f"filesystem source unavailable: {self.root}")
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=self.follow_symlinks):
            if not self.follow_symlinks:
                dirnames[:] = [name for name in dirnames if not (Path(dirpath) / name).is_symlink()]
            for name in filenames:
                path = Path(dirpath) / name
                relative = path.relative_to(self.root).as_posix()
                if any(fnmatch.fnmatch(relative, pattern) for pattern in self.exclude_globs):
                    continue
                if self.include_extensions and path.suffix.lower() not in self.include_extensions:
                    continue
                if path.is_symlink() and not self.follow_symlinks:
                    continue
                resolved = path.resolve()
                if self.root not in resolved.parents and resolved != self.root:
                    raise ValidationError(f"path escaped source root: {path}")
                stat_before = path.stat()
                if stat_before.st_size > self.max_file_bytes:
                    continue
                if self.settle_seconds > 0:
                    time.sleep(min(self.settle_seconds, 0.05))
                    stat_after = path.stat()
                    if (stat_before.st_size, stat_before.st_mtime_ns) != (
                        stat_after.st_size,
                        stat_after.st_mtime_ns,
                    ):
                        continue
                yield FileRecord(
                    path=path,
                    relative_path=relative,
                    size=stat_before.st_size,
                    mtime_ns=stat_before.st_mtime_ns,
                    sha256=self._hash(path),
                )

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
