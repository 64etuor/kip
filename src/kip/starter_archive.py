from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from kip.domain.starter_archive import (
    StarterArchiveManifest,
    StarterArchiveReceipt,
    StarterArchiveSource,
)
from kip.errors import ConflictError, ValidationError
from kip.starter_archive_policy import (
    scan_content,
    selected_source_files,
)
from kip.starter_archive_verifier import verify_starter_archive as verify_starter_archive

ZIP_EPOCH: Final = 315532800
MANIFEST_NAME: Final = "STARTER-KIT-MANIFEST.json"
CHECKSUM_NAME: Final = "SHA256SUMS"


@dataclass(frozen=True, slots=True)
class StarterArchiveBuildOptions:
    root: Path
    output: Path
    allow_dirty: bool = False
    source_date_epoch: int | None = None


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    relative: str
    content: bytes
    executable: bool = False


def build_starter_archive(options: StarterArchiveBuildOptions) -> StarterArchiveReceipt:
    root = options.root.expanduser().resolve()
    output = options.output.expanduser().resolve()
    if output.exists() or _sidecar(output).exists():
        raise ConflictError(f"starter archive output already exists: {output}")
    version = _version(root)
    expected_root = f"kip-starter-kit-{version}"
    commit, tracked_changes = _source_state(root)
    if tracked_changes and not options.allow_dirty:
        raise ConflictError("tracked or untracked source changes require --allow-dirty")
    epoch = _source_epoch(options, root)
    entries = _source_entries(root)
    digests = {entry.relative: f"sha256:{_sha256(entry.content)}" for entry in entries}
    manifest = StarterArchiveManifest(
        version=version,
        created_at=datetime.fromtimestamp(epoch, UTC),
        root=expected_root,
        files=digests,
        source=StarterArchiveSource(
            git_commit=commit,
            tracked_changes=tracked_changes,
        ),
    )
    manifest_entry = ArchiveEntry(
        MANIFEST_NAME,
        _manifest_bytes(manifest),
    )
    checksum_entries = (*entries, manifest_entry)
    checksum_entry = ArchiveEntry(CHECKSUM_NAME, _checksum_bytes(checksum_entries))
    archive_entries = tuple(
        ArchiveEntry(
            relative=f"{expected_root}/{entry.relative}",
            content=entry.content,
            executable=entry.executable,
        )
        for entry in (*checksum_entries, checksum_entry)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kip-starter-archive-", dir=output.parent) as temp:
        candidate = Path(temp) / output.name
        _write_zip(candidate, archive_entries, epoch)
        verified = verify_starter_archive(candidate)
        digest = verified.archive_sha256
        sidecar = Path(temp) / _sidecar(output).name
        sidecar.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
        os.replace(candidate, output)
        os.replace(sidecar, _sidecar(output))
    return verified.model_copy(
        update={"archive": str(output), "status": "built"}
    )


def default_starter_archive_output(root: Path) -> Path:
    return root / "dist" / f"kip-starter-kit-{_version(root)}.zip"


def _source_entries(root: Path) -> tuple[ArchiveEntry, ...]:
    entries: list[ArchiveEntry] = []
    for path in selected_source_files(root):
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        scan_content(content, relative)
        entries.append(
            ArchiveEntry(
                relative=relative,
                content=content,
                executable=bool(path.stat().st_mode & stat.S_IXUSR),
            )
        )
    return tuple(entries)


def _write_zip(
    output: Path,
    entries: tuple[ArchiveEntry, ...],
    epoch: int,
) -> None:
    timestamp = datetime.fromtimestamp(epoch, UTC).timetuple()[:6]
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for entry in sorted(entries, key=lambda item: item.relative):
            info = zipfile.ZipInfo(entry.relative, date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            mode = 0o755 if entry.executable else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            zipped.writestr(info, entry.content, compresslevel=9)


def _source_state(root: Path) -> tuple[str, bool]:
    commit = _git(root, ("rev-parse", "HEAD")) or "unknown"
    status_text = _git(root, ("status", "--porcelain", "--untracked-files=normal"))
    return commit, bool(status_text)


def _source_epoch(options: StarterArchiveBuildOptions, root: Path) -> int:
    value = options.source_date_epoch
    if value is None:
        configured = os.environ.get("SOURCE_DATE_EPOCH")
        value = int(configured) if configured is not None else None
    if value is None:
        commit_epoch = _git(root, ("show", "-s", "--format=%ct", "HEAD"))
        value = int(commit_epoch) if commit_epoch.isdigit() else ZIP_EPOCH
    if value < ZIP_EPOCH:
        raise ValidationError("source date epoch predates the ZIP format")
    return value


def _git(root: Path, arguments: tuple[str, ...]) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _version(root: Path) -> str:
    try:
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ValidationError("starter source VERSION is unavailable") from error
    if not re.fullmatch(r"[0-9A-Za-z._-]+", version):
        raise ValidationError("VERSION is not safe for a starter archive root")
    return version


def _checksum_bytes(entries: tuple[ArchiveEntry, ...]) -> bytes:
    lines = [f"{_sha256(entry.content)}  {entry.relative}" for entry in entries]
    return ("\n".join(lines) + "\n").encode()


def _manifest_bytes(manifest: StarterArchiveManifest) -> bytes:
    payload = manifest.model_dump(mode="json")
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sidecar(output: Path) -> Path:
    return output.with_name(f"{output.name}.sha256")
