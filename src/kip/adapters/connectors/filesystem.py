from __future__ import annotations

import fnmatch
import os
import stat
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from kip.adapters.storage.cloud_files import is_cloud_placeholder
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
        # from ingestion by a config filter, `max_file_bytes`, or the settle
        # window. Deletion reconciliation must treat these as "seen" so a
        # present file is never tombstoned as deleted.
        self.skipped_present_relative_paths: frozenset[str] = frozenset()
        self.skipped_reason_counts: dict[str, int] = {}
        self.deferred_relative_prefixes: frozenset[str] = frozenset()

    def scan(
        self,
        *,
        include_extensions: set[str] | None = None,
    ) -> Iterator[FileRecord]:
        # Publish diagnostics only on completion, never from an older scan.
        self.skipped_present_relative_paths = frozenset()
        self.skipped_reason_counts = {}
        self.deferred_relative_prefixes = frozenset()
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
        skipped_present: set[str] = set()
        skipped_reasons: Counter[str] = Counter()
        deferred_prefixes: set[str] = set()
        walk_errors: list[OSError] = []

        def skip_file(relative: str, reason: str) -> None:
            skipped_present.add(relative)
            skipped_reasons[reason] += 1

        # Cloud providers can mark directories dataless while their children
        # are resident. Enumerate metadata and check each file's content state.
        for dirpath, dirnames, filenames in os.walk(
            self.root,
            onerror=walk_errors.append,
            followlinks=self.follow_symlinks,
        ):
            current = Path(dirpath)
            # os.walk(followlinks=True) does not detect cycles or confine links.
            # Prune before descent; descendants are unknown, so deletion is deferred.
            ancestors = {
                ancestor.resolve()
                for ancestor in (current, *current.parents)
                if ancestor.is_relative_to(self.root)
            }
            allowed_directories: list[str] = []
            for name in dirnames:
                directory = current / name
                reason = ""
                if directory.is_symlink() and not self.follow_symlinks:
                    reason = "directory_symlink"
                else:
                    resolved_directory = directory.resolve()
                    if not resolved_directory.is_relative_to(self.root):
                        reason = "directory_outside_root"
                    elif resolved_directory in ancestors:
                        reason = "directory_cycle"
                if reason:
                    deferred_prefixes.add(directory.relative_to(self.root).as_posix())
                    skipped_reasons[reason] += 1
                else:
                    allowed_directories.append(name)
            dirnames[:] = allowed_directories
            for name in filenames:
                path = Path(dirpath) / name
                relative = path.relative_to(self.root).as_posix()
                if restrict_extensions and path.suffix.lower() not in target_extensions:
                    skip_file(relative, "extension_filter")
                    continue
                if any(fnmatch.fnmatch(relative, pattern) for pattern in self.exclude_globs):
                    skip_file(relative, "exclude_filter")
                    continue
                if path.is_symlink() and not self.follow_symlinks:
                    skip_file(relative, "file_symlink")
                    continue
                resolved = path.resolve()
                if self.root not in resolved.parents and resolved != self.root:
                    raise ValidationError(f"path escaped source root: {path}")
                stat_before = path.stat()
                if not stat.S_ISREG(stat_before.st_mode):
                    skip_file(relative, "non_regular")
                    continue
                if is_cloud_placeholder(stat_before):
                    skip_file(relative, "cloud_placeholder")
                    continue
                if stat_before.st_size > self.max_file_bytes:
                    skip_file(relative, "max_file_bytes")
                    continue
                if self.settle_seconds > 0:
                    age_ns = time.time_ns() - stat_before.st_mtime_ns
                    if 0 <= age_ns < int(self.settle_seconds * 1_000_000_000):
                        # Modified inside the settle window; the next scan
                        # picks it up without sleeping the whole walk.
                        skip_file(relative, "settle_window")
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
                            skip_file(relative, "unstable_file")
                            continue
                # The content hash is computed lazily on first access so
                # unchanged files can be skipped by size/mtime alone.
                yield FileRecord(
                    path=path,
                    relative_path=relative,
                    size=stat_before.st_size,
                    mtime_ns=stat_before.st_mtime_ns,
                )
        if walk_errors:
            raise SourceUnavailableError("filesystem scan incomplete") from walk_errors[0]
        self.skipped_present_relative_paths = frozenset(skipped_present)
        self.skipped_reason_counts = dict(skipped_reasons)
        self.deferred_relative_prefixes = frozenset(deferred_prefixes)
