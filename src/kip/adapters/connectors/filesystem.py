from __future__ import annotations

import fnmatch
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
        # Populated by the most recent completed `scan()` call: relative
        # paths of files that are still present on disk but were excluded
        # from ingestion by a config filter (extension/exclude-glob) or by
        # `max_file_bytes`. Deletion reconciliation must treat these as
        # "seen" so a file that merely grew too big, or fell outside a
        # narrowed include/exclude filter, is never tombstoned as deleted.
        self.skipped_present_relative_paths: frozenset[str] = frozenset()

    def scan(
        self,
        *,
        include_extensions: set[str] | None = None,
    ) -> Iterator[FileRecord]:
        if not self.root.exists() or not self.root.is_dir():
            raise SourceUnavailableError(f"filesystem source unavailable: {self.root}")
        target_extensions = self.include_extensions
        restrict_extensions = bool(target_extensions)
        if include_extensions is not None:
            requested = {value.lower() for value in include_extensions}
            target_extensions = (
                target_extensions.intersection(requested)
                if target_extensions
                else requested
            )
            restrict_extensions = True
        # Reset for this scan; only reassigned to the populated set once the
        # walk below completes, so a partially-consumed scan reports empty
        # (never stale data from a prior scan) rather than partial data.
        self.skipped_present_relative_paths = frozenset()
        skipped_present: set[str] = set()
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=self.follow_symlinks):
            if not self.follow_symlinks:
                dirnames[:] = [name for name in dirnames if not (Path(dirpath) / name).is_symlink()]
            for name in filenames:
                path = Path(dirpath) / name
                relative = path.relative_to(self.root).as_posix()
                if restrict_extensions and path.suffix.lower() not in target_extensions:
                    skipped_present.add(relative)
                    continue
                if any(fnmatch.fnmatch(relative, pattern) for pattern in self.exclude_globs):
                    skipped_present.add(relative)
                    continue
                if path.is_symlink() and not self.follow_symlinks:
                    continue
                resolved = path.resolve()
                if self.root not in resolved.parents and resolved != self.root:
                    raise ValidationError(f"path escaped source root: {path}")
                stat_before = path.stat()
                if stat_before.st_size > self.max_file_bytes:
                    skipped_present.add(relative)
                    continue
                if self.settle_seconds > 0:
                    age_ns = time.time_ns() - stat_before.st_mtime_ns
                    if 0 <= age_ns < int(self.settle_seconds * 1_000_000_000):
                        # Modified inside the settle window; the next scan
                        # picks it up without sleeping the whole walk.
                        continue
                    if age_ns < 0:
                        # Future mtime (clock skew): fall back to a short
                        # stability probe instead of trusting the timestamp.
                        time.sleep(min(self.settle_seconds, 0.05))
                        stat_after = path.stat()
                        if (stat_before.st_size, stat_before.st_mtime_ns) != (
                            stat_after.st_size,
                            stat_after.st_mtime_ns,
                        ):
                            continue
                # The content hash is computed lazily on first access so
                # unchanged files can be skipped by size/mtime alone.
                yield FileRecord(
                    path=path,
                    relative_path=relative,
                    size=stat_before.st_size,
                    mtime_ns=stat_before.st_mtime_ns,
                )
        self.skipped_present_relative_paths = frozenset(skipped_present)

