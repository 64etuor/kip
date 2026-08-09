from __future__ import annotations

import os
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from kip.errors import ValidationError
from kip.setup.models import FilesystemSourceAnswer, SourceInventory


def inspect_source(
    source: FilesystemSourceAnswer,
    *,
    max_entries: int = 1_000_000,
) -> SourceInventory:
    root = Path(source.root)
    file_count = 0
    byte_count = 0
    extension_counts: dict[str, int] = {}
    excluded_count = 0
    symlink_count = 0
    unreadable_count = 0
    visited_entries = 0

    for directory, directory_names, file_names in os.walk(
        root,
        followlinks=False,
    ):
        base = Path(directory)
        retained_directories: list[str] = []
        for name in directory_names:
            path = base / name
            relative = path.relative_to(root).as_posix()
            visited_entries += 1
            if visited_entries > max_entries:
                raise ValidationError("source inventory exceeded the entry limit")
            if path.is_symlink():
                symlink_count += 1
                excluded_count += 1
            elif _excluded(relative, source.exclude_globs):
                excluded_count += 1
            else:
                retained_directories.append(name)
        directory_names[:] = retained_directories

        for name in file_names:
            path = base / name
            relative = path.relative_to(root).as_posix()
            visited_entries += 1
            if visited_entries > max_entries:
                raise ValidationError("source inventory exceeded the entry limit")
            if path.is_symlink():
                symlink_count += 1
                excluded_count += 1
                continue
            extension = path.suffix.lower()
            if _excluded(relative, source.exclude_globs) or (
                extension not in source.include_extensions
            ):
                excluded_count += 1
                continue
            try:
                size = path.stat(follow_symlinks=False).st_size
            except OSError:
                unreadable_count += 1
                continue
            file_count += 1
            byte_count += size
            extension_counts[extension or "<none>"] = (
                extension_counts.get(extension or "<none>", 0) + 1
            )

    return SourceInventory(
        root=str(root),
        file_count=file_count,
        byte_count=byte_count,
        extension_counts=dict(sorted(extension_counts.items())),
        excluded_count=excluded_count,
        symlink_count=symlink_count,
        unreadable_count=unreadable_count,
    )


def _excluded(relative: str, patterns: list[str]) -> bool:
    path = PurePosixPath(relative)
    return any(
        path.match(pattern) or fnmatch(relative, pattern)
        for pattern in patterns
    )
