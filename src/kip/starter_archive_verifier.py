from __future__ import annotations

import hashlib
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Final

from kip.domain.starter_archive import StarterArchiveManifest, StarterArchiveReceipt
from kip.errors import ValidationError
from kip.starter_archive_policy import (
    REQUIRED_FILES,
    scan_content,
    validate_included_path,
    validate_relative_path,
)

type DigestMap = dict[str, str]
MAX_FILES: Final = 5000
MAX_ENTRY_BYTES: Final = 32 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 256 * 1024 * 1024
MANIFEST_NAME: Final = "STARTER-KIT-MANIFEST.json"
CHECKSUM_NAME: Final = "SHA256SUMS"


def verify_starter_archive(archive: Path) -> StarterArchiveReceipt:
    path = archive.expanduser().resolve()
    contents, root = _read_archive(path)
    manifest = _manifest(contents, root)
    payload = set(contents) - {MANIFEST_NAME, CHECKSUM_NAME}
    _validate_payload(payload, manifest)
    _validate_checksums(contents, manifest)
    archive_digest = _sha256(path.read_bytes())
    _verify_sidecar(path, archive_digest)
    return StarterArchiveReceipt(
        archive=str(path),
        archive_sha256=archive_digest,
        file_count=len(payload),
        root=root,
        status="verified",
        version=manifest.version,
    )


def _read_archive(path: Path) -> tuple[dict[str, bytes], str]:
    try:
        with zipfile.ZipFile(path) as zipped:
            infos = zipped.infolist()
            _validate_infos(infos)
            root = _single_root(infos)
            contents = {_relative_name(info, root): zipped.read(info) for info in infos}
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise ValidationError(f"invalid starter archive: {error}") from error
    return contents, root


def _manifest(contents: dict[str, bytes], root: str) -> StarterArchiveManifest:
    manifest_bytes = contents.get(MANIFEST_NAME)
    if manifest_bytes is None or CHECKSUM_NAME not in contents:
        raise ValidationError("starter archive manifest or checksums are missing")
    manifest = StarterArchiveManifest.model_validate_json(manifest_bytes)
    if manifest.root != root:
        raise ValidationError("starter archive root does not match the manifest")
    return manifest


def _validate_payload(payload: set[str], manifest: StarterArchiveManifest) -> None:
    for relative in payload:
        validate_included_path(PurePosixPath(relative))
    if payload != set(manifest.files):
        raise ValidationError("starter archive manifest does not cover the payload")
    missing = REQUIRED_FILES - payload
    if missing:
        raise ValidationError(f"required starter files are missing: {sorted(missing)}")


def _validate_checksums(
    contents: dict[str, bytes],
    manifest: StarterArchiveManifest,
) -> None:
    checksums = _parse_checksums(contents[CHECKSUM_NAME])
    expected_checksums = set(contents) - {CHECKSUM_NAME}
    if set(checksums) != expected_checksums:
        raise ValidationError("starter checksums do not cover the payload")
    for relative, content in contents.items():
        if relative == CHECKSUM_NAME:
            continue
        digest = _sha256(content)
        if checksums[relative] != digest:
            raise ValidationError(f"starter checksum mismatch: {relative}")
        if relative != MANIFEST_NAME and manifest.files[relative] != f"sha256:{digest}":
            raise ValidationError(f"starter manifest digest mismatch: {relative}")
        scan_content(content, relative)


def _validate_infos(infos: list[zipfile.ZipInfo]) -> None:
    if not infos or len(infos) > MAX_FILES:
        raise ValidationError("starter archive file count is invalid")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ValidationError("starter archive contains duplicate paths")
    total = 0
    for info in infos:
        pure = PurePosixPath(info.filename)
        if info.is_dir() or info.flag_bits & 0x1:
            raise ValidationError(f"unsupported starter archive entry: {info.filename}")
        if pure.is_absolute() or ".." in pure.parts or len(pure.parts) < 2:
            raise ValidationError(f"unsafe starter archive entry: {info.filename}")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ValidationError(f"starter archive contains a symlink: {info.filename}")
        if info.file_size > MAX_ENTRY_BYTES:
            raise ValidationError(f"starter archive entry is too large: {info.filename}")
        total += info.file_size
    if total > MAX_TOTAL_BYTES:
        raise ValidationError("starter archive uncompressed size is too large")


def _single_root(infos: list[zipfile.ZipInfo]) -> str:
    roots = {PurePosixPath(info.filename).parts[0] for info in infos}
    if len(roots) != 1:
        raise ValidationError("starter archive must contain exactly one root directory")
    return roots.pop()


def _relative_name(info: zipfile.ZipInfo, root: str) -> str:
    relative = PurePosixPath(info.filename).relative_to(root)
    validate_relative_path(relative)
    return relative.as_posix()


def _parse_checksums(content: bytes) -> DigestMap:
    parsed: DigestMap = {}
    for line in content.decode("utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValidationError("starter checksum entry is invalid")
        validate_relative_path(PurePosixPath(relative))
        if relative in parsed:
            raise ValidationError("starter checksum path is duplicated")
        parsed[relative] = digest
    return parsed


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sidecar(output: Path) -> Path:
    return output.with_name(f"{output.name}.sha256")


def _verify_sidecar(archive: Path, archive_digest: str) -> None:
    sidecar = _sidecar(archive)
    if not sidecar.exists():
        return
    lines = sidecar.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1:
        raise ValidationError("starter external digest file is invalid")
    digest, separator, name = lines[0].partition("  ")
    if (
        not separator
        or name != archive.name
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or digest != archive_digest
    ):
        raise ValidationError("starter external digest does not match the archive")
