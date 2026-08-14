#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO, Any

CAS_MANIFEST_SCHEMA = "kip.cas-backup-manifest.v1"
DATABASE_MANIFEST_SCHEMA = "kip.database-backup-manifest.v1"
BACKUP_MANIFEST_SCHEMA = "kip.backup-manifest.v1"
CONFIG_PATHS = (
    "VERSION",
    "config/kip.container.toml",
    "config/kip.example.toml",
    "config/kip.toml",
    "config/logging.yaml",
    "deploy/sql/roles.sql.template",
    "migrations",
    "ontology",
)
REDACTED_VALUE = "[REDACTED]"
REDACTABLE_CONFIG_SUFFIXES = (".toml", ".yaml", ".yml")
_SECRET_KEY_PATTERN = re.compile(
    r"(?i)(?:password|passwd|secret|token|credential|api_key|apikey|access_key|private_key)"
)
_REFERENCE_KEY_SUFFIXES = ("_env", "_file")
_ASSIGNMENT_PATTERN = re.compile(
    r'^(?P<prefix>\s*(?P<key>[A-Za-z0-9_.-]+)\s*[=:]\s*)"(?P<value>[^"]*)"(?P<suffix>\s*(?:#.*)?)$'
)
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.-]*://[^/:@\s\"']+):(?P<password>[^@\s\"']+)@"
)
REQUIRED_PAYLOADS = frozenset(
    {
        "canonical-export-receipt.json",
        "canonical.jsonl",
        "cas-manifest.json",
        "cas.tar.gz",
        "configuration.tar.gz",
        "database-manifest.json",
        "kip.dump",
    }
)


class BackupError(RuntimeError):
    pass


def _created_at() -> datetime:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is None:
        return datetime.now(UTC).replace(microsecond=0)
    try:
        return datetime.fromtimestamp(int(epoch), UTC)
    except (OverflowError, ValueError) as error:
        raise BackupError("SOURCE_DATE_EPOCH must be an integer timestamp") from error


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise BackupError(f"refusing to overwrite backup artifact: {path.name}")
    path.write_bytes(_json_bytes(value))
    path.chmod(0o600)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stream_sha256(stream: IO[bytes]) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _regular_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise BackupError(f"backup source must be a non-symlink directory: {root}")
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BackupError(f"backup source contains a symlink: {path.relative_to(root)}")
        metadata = path.stat()
        if stat.S_ISREG(metadata.st_mode):
            files.append(path)
        elif not stat.S_ISDIR(metadata.st_mode):
            raise BackupError(f"backup source contains a special file: {path.relative_to(root)}")
    return files


def _safe_member_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise BackupError(f"archive contains an unsafe path: {name}")
    return path


@contextmanager
def _tar_writer(path: Path, epoch: int) -> Iterator[tarfile.TarFile]:
    if path.exists():
        raise BackupError(f"refusing to overwrite backup artifact: {path.name}")
    with (
        path.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        yield archive


def _is_reference_value(value: str) -> bool:
    return value.startswith(("env:", "file:")) or value == REDACTED_VALUE


def _is_reference_key(key: str) -> bool:
    return key.lower().endswith(_REFERENCE_KEY_SUFFIXES)


def redact_config_text(text: str) -> tuple[str, int]:
    """Redact literal secret values from a configuration document.

    The configuration convention stores secrets as ``env:``/``file:``
    references or ``*_env``/``*_file`` indirection keys; those are preserved.
    A quoted literal value assigned to a secret-looking key, and any URL
    userinfo password, are replaced with ``[REDACTED]``.
    """
    redacted = 0
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        newline = line[len(body) :]
        match = _ASSIGNMENT_PATTERN.match(body)
        if match:
            key = match.group("key")
            value = match.group("value")
            if (
                _SECRET_KEY_PATTERN.search(key)
                and not _is_reference_key(key)
                and not _is_reference_value(value)
                and value
            ):
                body = f'{match.group("prefix")}"{REDACTED_VALUE}"{match.group("suffix")}'
                redacted += 1
                lines.append(body + newline)
                continue
        body, url_count = _URL_CREDENTIAL_PATTERN.subn(rf"\g<scheme>:{REDACTED_VALUE}@", body)
        redacted += url_count
        lines.append(body + newline)
    return "".join(lines), redacted


def _contains_literal_secret(text: str) -> str | None:
    """Return a description of the first literal secret found, if any."""
    for number, line in enumerate(text.splitlines(), start=1):
        match = _ASSIGNMENT_PATTERN.match(line)
        if match:
            key = match.group("key")
            value = match.group("value")
            if (
                _SECRET_KEY_PATTERN.search(key)
                and not _is_reference_key(key)
                and not _is_reference_value(value)
                and value
            ):
                return f"line {number}: literal value for secret key {key!r}"
        cleaned = _URL_CREDENTIAL_PATTERN.search(line)
        if cleaned and cleaned.group("password") != REDACTED_VALUE:
            return f"line {number}: URL contains embedded credentials"
    return None


def _add_file(archive: tarfile.TarFile, source: Path, relative: str, epoch: int) -> None:
    metadata = source.stat()
    info = tarfile.TarInfo(relative)
    info.size = metadata.st_size
    info.mode = 0o600
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = epoch
    with source.open("rb") as stream:
        archive.addfile(info, stream)


def _add_bytes(archive: tarfile.TarFile, payload: bytes, relative: str, epoch: int) -> None:
    info = tarfile.TarInfo(relative)
    info.size = len(payload)
    info.mode = 0o600
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = epoch
    archive.addfile(info, io.BytesIO(payload))


def snapshot_cas(source: Path, archive_path: Path, manifest_path: Path) -> dict[str, Any]:
    if source.is_symlink():
        raise BackupError("CAS backup source must not be a symlink")
    source = source.resolve()
    files = _regular_files(source)
    created = _created_at()
    records = [
        {
            "path": path.relative_to(source).as_posix(),
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
        for path in files
    ]
    try:
        with _tar_writer(archive_path, int(created.timestamp())) as archive:
            for path in files:
                _add_file(
                    archive,
                    path,
                    path.relative_to(source).as_posix(),
                    int(created.timestamp()),
                )
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    manifest = {
        "schema_version": CAS_MANIFEST_SCHEMA,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "file_count": len(records),
        "total_bytes": sum(path.stat().st_size for path in files),
        "files": records,
    }
    _write_json(manifest_path, manifest)
    return manifest


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BackupError(f"invalid JSON artifact: {path.name}") from error
    if not isinstance(value, dict):
        raise BackupError(f"JSON artifact must contain an object: {path.name}")
    return value


def _cas_records(manifest_path: Path) -> dict[str, dict[str, Any]]:
    manifest = _load_json(manifest_path)
    if manifest.get("schema_version") != CAS_MANIFEST_SCHEMA:
        raise BackupError("CAS manifest schema is invalid")
    records: dict[str, dict[str, Any]] = {}
    for raw in manifest.get("files", []):
        if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
            raise BackupError("CAS manifest contains an invalid record")
        relative = _safe_member_name(raw["path"]).as_posix()
        if relative in records:
            raise BackupError("CAS manifest contains duplicate paths")
        if not isinstance(raw.get("size"), int) or not isinstance(raw.get("sha256"), str):
            raise BackupError("CAS manifest contains invalid size or digest")
        records[relative] = raw
    if manifest.get("file_count") != len(records):
        raise BackupError("CAS manifest file count is invalid")
    return records


def verify_cas(archive_path: Path, manifest_path: Path) -> dict[str, Any]:
    records = _cas_records(manifest_path)
    seen: set[str] = set()
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                relative = _safe_member_name(member.name).as_posix()
                if not member.isfile() or member.issym() or member.islnk():
                    raise BackupError("CAS archive may contain regular files only")
                if relative in seen or relative not in records:
                    raise BackupError("CAS archive contains an unexpected or duplicate file")
                stream = archive.extractfile(member)
                if stream is None:
                    raise BackupError("CAS archive member is unreadable")
                record = records[relative]
                if member.size != record["size"] or _stream_sha256(stream) != record["sha256"]:
                    raise BackupError(f"CAS archive checksum mismatch: {relative}")
                seen.add(relative)
    except (OSError, tarfile.TarError) as error:
        raise BackupError("CAS archive is invalid") from error
    if seen != set(records):
        raise BackupError("CAS archive does not contain every manifest object")
    return {"file_count": len(seen), "status": "verified"}


def restore_cas(archive_path: Path, manifest_path: Path, target: Path) -> dict[str, Any]:
    verification = verify_cas(archive_path, manifest_path)
    if target.is_symlink():
        raise BackupError("CAS restore target must not be a symlink")
    target = target.resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise BackupError("CAS restore target must be absent or an empty directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.restore-", dir=target.parent))
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                relative = _safe_member_name(member.name)
                destination = staging.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise BackupError("CAS archive member is unreadable")
                with destination.open("xb") as output:
                    shutil.copyfileobj(stream, output)
                destination.chmod(0o600)
        restored = _regular_files(staging)
        if len(restored) != verification["file_count"]:
            raise BackupError("restored CAS object count is invalid")
        if target.exists():
            target.rmdir()
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {**verification, "target": str(target)}


def snapshot_config(root: Path, archive_path: Path) -> dict[str, Any]:
    if root.is_symlink():
        raise BackupError("configuration root must not be a symlink")
    root = root.resolve()
    selected: list[Path] = []
    for relative in CONFIG_PATHS:
        path = root / relative
        if not path.exists():
            continue
        if path.is_symlink():
            raise BackupError(f"configuration snapshot contains a symlink: {relative}")
        if path.is_dir():
            selected.extend(_regular_files(path))
        elif path.is_file():
            selected.append(path)
        else:
            raise BackupError(f"configuration snapshot contains a special file: {relative}")
    if not selected:
        raise BackupError("configuration snapshot has no files")
    created = _created_at()
    redacted_values = 0
    try:
        with _tar_writer(archive_path, int(created.timestamp())) as archive:
            for path in sorted(set(selected)):
                relative = path.relative_to(root).as_posix()
                if relative.startswith("config/") and path.suffix in REDACTABLE_CONFIG_SUFFIXES:
                    try:
                        text = path.read_text(encoding="utf-8")
                    except UnicodeError as error:
                        raise BackupError(
                            f"configuration file is not valid UTF-8: {relative}"
                        ) from error
                    text, count = redact_config_text(text)
                    redacted_values += count
                    _add_bytes(archive, text.encode(), relative, int(created.timestamp()))
                else:
                    _add_file(archive, path, relative, int(created.timestamp()))
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return {
        "file_count": len(set(selected)),
        "redacted_values": redacted_values,
        "status": "created",
    }


def _verify_config_archive(path: Path) -> None:
    seen: set[str] = set()
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                relative = _safe_member_name(member.name).as_posix()
                if not member.isfile() or member.issym() or member.islnk() or relative in seen:
                    raise BackupError("configuration archive contains an unsafe member")
                seen.add(relative)
                if relative.startswith("config/") and relative.endswith(REDACTABLE_CONFIG_SUFFIXES):
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise BackupError("configuration archive member is unreadable")
                    try:
                        text = stream.read().decode("utf-8")
                    except UnicodeError as error:
                        raise BackupError(
                            f"configuration archive member is not UTF-8: {relative}"
                        ) from error
                    finding = _contains_literal_secret(text)
                    if finding is not None:
                        raise BackupError(
                            f"configuration snapshot contains a literal secret ({relative}, {finding})"
                        )
    except (OSError, tarfile.TarError) as error:
        raise BackupError("configuration archive is invalid") from error
    if "VERSION" not in seen or not any(name.startswith("ontology/") for name in seen):
        raise BackupError("configuration archive is incomplete")


def _database_manifest(path: Path) -> dict[str, Any]:
    manifest = _load_json(path)
    if manifest.get("schema_version") != DATABASE_MANIFEST_SCHEMA:
        raise BackupError("database manifest schema is invalid")
    if manifest.get("row_security") != "off":
        raise BackupError("database manifest was not captured with row_security=off")
    if not isinstance(manifest.get("migrations"), list) or not isinstance(
        manifest.get("counts"), dict
    ):
        raise BackupError("database manifest is incomplete")
    return manifest


def _parse_checksums(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if (
            not separator
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise BackupError("SHA256SUMS contains an invalid entry")
        safe = _safe_member_name(relative).as_posix()
        if safe in entries:
            raise BackupError("SHA256SUMS contains duplicate paths")
        entries[safe] = digest
    return entries


def seal_backup(root: Path) -> dict[str, Any]:
    if root.is_symlink():
        raise BackupError("backup set must not be a symlink")
    root = root.resolve()
    if root.is_symlink() or not root.is_dir():
        raise BackupError("backup set must be a non-symlink directory")
    if (root / "backup-manifest.json").exists() or (root / "SHA256SUMS").exists():
        raise BackupError("backup set is already sealed")
    files = {path.relative_to(root).as_posix(): path for path in _regular_files(root)}
    missing = REQUIRED_PAYLOADS - set(files)
    if missing:
        raise BackupError(f"backup set is missing required artifacts: {', '.join(sorted(missing))}")
    database = _database_manifest(root / "database-manifest.json")
    verify_cas(root / "cas.tar.gz", root / "cas-manifest.json")
    _verify_config_archive(root / "configuration.tar.gz")
    created = _created_at()
    manifest = {
        "schema_version": BACKUP_MANIFEST_SCHEMA,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "database": {
            "name": database.get("database"),
            "server_version_num": database.get("server_version_num"),
        },
        "payloads": {
            relative: {"sha256": _sha256(path), "size": path.stat().st_size}
            for relative, path in sorted(files.items())
        },
    }
    _write_json(root / "backup-manifest.json", manifest)
    checksum_files = {path.relative_to(root).as_posix(): path for path in _regular_files(root)}
    sums = "".join(
        f"{_sha256(path)}  {relative}\n" for relative, path in sorted(checksum_files.items())
    )
    checksums = root / "SHA256SUMS"
    checksums.write_text(sums, encoding="utf-8")
    checksums.chmod(0o600)
    return {"file_count": len(checksum_files) + 1, "status": "sealed"}


def verify_backup(root: Path) -> dict[str, Any]:
    if root.is_symlink():
        raise BackupError("backup set must not be a symlink")
    root = root.resolve()
    files = {path.relative_to(root).as_posix(): path for path in _regular_files(root)}
    if "SHA256SUMS" not in files or "backup-manifest.json" not in files:
        raise BackupError("backup set is not sealed")
    checksums = _parse_checksums(root / "SHA256SUMS")
    if set(checksums) != set(files) - {"SHA256SUMS"}:
        raise BackupError("SHA256SUMS does not cover the complete backup set")
    for relative, digest in checksums.items():
        if _sha256(files[relative]) != digest:
            raise BackupError(f"backup checksum mismatch: {relative}")
    manifest = _load_json(root / "backup-manifest.json")
    if manifest.get("schema_version") != BACKUP_MANIFEST_SCHEMA:
        raise BackupError("backup manifest schema is invalid")
    payloads = manifest.get("payloads")
    expected = set(files) - {"SHA256SUMS", "backup-manifest.json"}
    if not isinstance(payloads, dict) or set(payloads) != expected:
        raise BackupError("backup manifest does not cover the payload")
    for relative, record in payloads.items():
        if not isinstance(record, dict) or record.get("sha256") != _sha256(files[relative]):
            raise BackupError(f"backup manifest digest mismatch: {relative}")
    _database_manifest(root / "database-manifest.json")
    verify_cas(root / "cas.tar.gz", root / "cas-manifest.json")
    _verify_config_archive(root / "configuration.tar.gz")
    return {"file_count": len(files), "status": "verified"}


def compare_database(expected_path: Path, actual_path: Path) -> dict[str, Any]:
    expected = _database_manifest(expected_path)
    actual = _database_manifest(actual_path)
    expected_major = int(expected.get("server_version_num", 0)) // 10000
    actual_major = int(actual.get("server_version_num", 0)) // 10000
    if expected_major != actual_major:
        raise BackupError("restored PostgreSQL major version does not match the backup")
    for field in ("migrations", "counts", "extensions", "rls_policy_count"):
        if expected.get(field) != actual.get(field):
            raise BackupError(f"restored database {field} do not match the backup")
    return {
        "database": actual.get("database"),
        "server_major": actual_major,
        "status": "verified",
    }


def compare_evaluation(baseline_path: Path, actual_path: Path) -> dict[str, Any]:
    baseline = _load_json(baseline_path)
    actual = _load_json(actual_path)
    if baseline.get("schema_version") != "kip.evaluation-report.v1":
        raise BackupError("baseline evaluation report schema is invalid")
    if actual.get("schema_version") != "kip.evaluation-report.v1":
        raise BackupError("restored evaluation report schema is invalid")
    baseline_run = baseline.get("run", {})
    actual_run = actual.get("run", {})
    for field in ("dataset", "dataset_version", "dataset_source_revision"):
        if baseline_run.get(field) != actual_run.get(field):
            raise BackupError(f"restored evaluation {field} does not match the baseline")
    baseline_variants = baseline.get("variants")
    actual_variants = actual.get("variants")
    if not isinstance(baseline_variants, dict) or not isinstance(actual_variants, dict):
        raise BackupError("evaluation report variants are invalid")
    if set(baseline_variants) != set(actual_variants):
        raise BackupError("restored evaluation variants do not match the baseline")
    case_fields = (
        "case_id",
        "error",
        "latest_version_match",
        "locator_match",
        "ndcg_at_k",
        "ranked_documents",
        "recall_at_k",
        "reciprocal_rank",
        "stale_warning_match",
        "unauthorized_result_count",
        "zero_results",
    )
    for name in sorted(baseline_variants):
        expected_variant = baseline_variants[name]
        actual_variant = actual_variants[name]
        if not isinstance(expected_variant, dict) or not isinstance(actual_variant, dict):
            raise BackupError("evaluation variant payload is invalid")
        for field in ("metrics", "categories", "answer_quality", "ontology_quality"):
            if expected_variant.get(field) != actual_variant.get(field):
                raise BackupError(f"restored evaluation {name}.{field} does not match baseline")
        expected_cases = [
            {field: case.get(field) for field in case_fields}
            for case in expected_variant.get("cases", [])
        ]
        actual_cases = [
            {field: case.get(field) for field in case_fields}
            for case in actual_variant.get("cases", [])
        ]
        if expected_cases != actual_cases:
            raise BackupError(f"restored evaluation {name}.cases do not match baseline")
    return {"status": "verified", "variants": sorted(baseline_variants)}


def write_receipt(kind: str, evidence: Path, output: Path) -> dict[str, Any]:
    if evidence.is_symlink():
        raise BackupError("recovery evidence must not be a symlink")
    evidence = evidence.resolve()
    if evidence.is_symlink() or not evidence.is_dir():
        raise BackupError("recovery evidence must be a non-symlink directory")
    output = output.resolve()
    files = {
        path.relative_to(evidence).as_posix(): path
        for path in _regular_files(evidence)
        if path.resolve() != output
    }
    if not files:
        raise BackupError("recovery evidence is empty")
    payload = {
        "schema_version": "kip.recovery-receipt.v1",
        "kind": kind,
        "created_at": _created_at().isoformat().replace("+00:00", "Z"),
        "evidence": {
            relative: {"sha256": _sha256(path), "size": path.stat().st_size}
            for relative, path in sorted(files.items())
        },
        "status": "verified",
    }
    _write_json(output, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot-cas")
    snapshot.add_argument("--source", type=Path, required=True)
    snapshot.add_argument("--archive", type=Path, required=True)
    snapshot.add_argument("--manifest", type=Path, required=True)
    restore = subparsers.add_parser("restore-cas")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--manifest", type=Path, required=True)
    restore.add_argument("--target", type=Path, required=True)
    config = subparsers.add_parser("snapshot-config")
    config.add_argument("--root", type=Path, required=True)
    config.add_argument("--archive", type=Path, required=True)
    seal = subparsers.add_parser("seal")
    seal.add_argument("root", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("root", type=Path)
    compare = subparsers.add_parser("compare-database")
    compare.add_argument("--expected", type=Path, required=True)
    compare.add_argument("--actual", type=Path, required=True)
    compare_evaluation_parser = subparsers.add_parser("compare-evaluation")
    compare_evaluation_parser.add_argument("--baseline", type=Path, required=True)
    compare_evaluation_parser.add_argument("--actual", type=Path, required=True)
    receipt = subparsers.add_parser("receipt")
    receipt.add_argument("--kind", choices=("restore", "restore-drill"), required=True)
    receipt.add_argument("--evidence", type=Path, required=True)
    receipt.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "snapshot-cas":
            payload = snapshot_cas(arguments.source, arguments.archive, arguments.manifest)
        elif arguments.command == "restore-cas":
            payload = restore_cas(arguments.archive, arguments.manifest, arguments.target)
        elif arguments.command == "snapshot-config":
            payload = snapshot_config(arguments.root, arguments.archive)
        elif arguments.command == "seal":
            payload = seal_backup(arguments.root)
        elif arguments.command == "verify":
            payload = verify_backup(arguments.root)
        elif arguments.command == "compare-database":
            payload = compare_database(arguments.expected, arguments.actual)
        elif arguments.command == "compare-evaluation":
            payload = compare_evaluation(arguments.baseline, arguments.actual)
        else:
            payload = write_receipt(arguments.kind, arguments.evidence, arguments.output)
    except (BackupError, OSError, ValueError) as error:
        print(f"backup verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
