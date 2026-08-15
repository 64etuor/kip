#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

IMAGE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/:+-]*@sha256:[0-9a-f]{64}$")
FORBIDDEN_PARTS = frozenset(
    {
        ".env",
        ".git",
        ".kip",
        ".omc",
        ".omo",
        ".playwright-cli",
        ".venv",
        "__pycache__",
        "cas",
        "output",
        "secrets",
        "var",
    }
)
FORBIDDEN_SUFFIXES = frozenset({".backup", ".db", ".dump", ".pyc", ".pyo", ".sqlite"})
PRIVATE_PATTERNS = (
    re.compile(r"/" + r"Users/[^/\s]+/"),
    re.compile(r"-{5}BEGIN [A-Z ]*PRIVATE KEY-{5}"),
    re.compile(r"A" + r"KIA[0-9A-Z]{16}"),
    re.compile(r"(?:g" + r"hp|github_pat|sk-ant|sk-proj)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"xo" + r"x[baprs]-[A-Za-z0-9-]{16,}"),
)
DATABASE_URL_PATTERN = re.compile(
    r"postgres(?:ql)?://[^:\s]+:([^@\s]+)@",
    re.IGNORECASE,
)
SAFE_EXAMPLE_PASSWORDS = frozenset(
    {
        "change-me",
        "change-me-before-use",
        "ci-test-secret-key-do-not-use-in-production",
        "fixture",
        "kip",
        "redacted",
        "test-password",
        "testpassword",
    }
)
ROOT_FILES = (
    ".dockerignore",
    ".env.example",
    ".gitignore",
    ".mcp.json",
    "AGENTS.md",
    "CLAUDE.md",
    "Dockerfile",
    "LICENSE",
    "Makefile",
    "README.md",
    "VERSION",
    "compose.production.yaml",
    "compose.yaml",
    "pyproject.toml",
    "uv.lock",
)
ROOT_DIRECTORIES = (
    ".claude",
    ".github",
    "contracts",
    "deploy",
    "docs",
    "examples",
    "migrations",
    "ontology",
    "requirements",
    "scripts",
    "sdk",
    "sample-data",
    "skills",
    "src",
    "tests",
)
EVALUATION_PATHS = (
    "README.md",
    "corpus",
    "experiments",
    "golden",
    "reviews",
    "schemas",
)
# Policy: exclude by CONTENT, not filename prefix. `private-onedrive-nl.yaml`
# and its `.floor.json` companion hold the real private OneDrive corpus
# (verbatim internal question text, real document IDs, a reviewer name) and
# must never leave this repository (docs/STARTER_KIT_GUIDE.md#8). By
# contrast `private-starter.yaml` is a deliberately redacted synthetic
# sample -- its own description says so, and `evaluation/README.md` and
# `docs/AI_OPERATOR_RUNBOOK.md` both instruct operators to run it as the
# starter-kit acceptance template -- so it is intentionally NOT excluded and
# ships with the starter bundle.
STARTER_EXCLUDED_PATHS = (
    "evaluation/golden/private-onedrive-nl.yaml",
    "evaluation/golden/private-onedrive-nl.floor.json",
)
CONFIG_FILES = ("kip.container.toml", "kip.example.toml", "logging.yaml")
COPY_IGNORED_NAMES = frozenset(
    {
        ".DS_Store",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "worktrees",
    }
)
REQUIRED_BUNDLE_FILES = (
    "RELEASE-MANIFEST.json",
    "SHA256SUMS",
    "artifacts/images.lock.json",
    "artifacts/provenance.intoto.json",
    "artifacts/sbom.spdx.json",
    "starter/AGENTS.md",
    "starter/CLAUDE.md",
    "starter/compose.production.yaml",
    "starter/contracts/evaluation-review-bundle.schema.json",
    "starter/contracts/golden-draft-review.schema.json",
    "starter/contracts/golden-draft.schema.json",
    "starter/contracts/setup-plan.schema.json",
    "starter/docs/STARTER_KIT_GUIDE.md",
    "starter/migrations/0012_query_traces.sql",
    "starter/migrations/0021_discovery_candidate_spec.sql",
    "starter/ontology/core/predicates.yaml",
    "starter/skills/kip-setup/SKILL.md",
)


class ReleaseError(RuntimeError):
    pass


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _copy_tree(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part in COPY_IGNORED_NAMES for part in relative.parts):
            continue
        if path.is_symlink():
            raise ReleaseError(f"source tree contains a symlink: {relative}")
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            *COPY_IGNORED_NAMES,
            "*.pyc",
            "*.pyo",
        ),
    )


def _copy_starter(root: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    for relative in ROOT_FILES:
        source = root / relative
        if not source.is_file():
            raise ReleaseError(f"required starter file is missing: {relative}")
        shutil.copy2(source, destination / relative)
    for relative in ROOT_DIRECTORIES:
        source = root / relative
        if not source.is_dir():
            raise ReleaseError(f"required starter directory is missing: {relative}")
        _copy_tree(source, destination / relative)
    config = destination / "config"
    config.mkdir()
    for name in CONFIG_FILES:
        shutil.copy2(root / "config" / name, config / name)
    evaluation = destination / "evaluation"
    evaluation.mkdir()
    for relative in EVALUATION_PATHS:
        source = root / "evaluation" / relative
        target = evaluation / relative
        if source.is_dir():
            _copy_tree(source, target)
        elif source.is_file():
            shutil.copy2(source, target)
    for excluded in STARTER_EXCLUDED_PATHS:
        excluded_path = destination / excluded
        if excluded_path.is_symlink() or excluded_path.is_file():
            excluded_path.unlink()


def _git_value(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _release_time() -> datetime:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is None:
        return datetime.now(UTC).replace(microsecond=0)
    try:
        return datetime.fromtimestamp(int(epoch), UTC)
    except (OverflowError, ValueError) as error:
        raise ReleaseError("SOURCE_DATE_EPOCH must be an integer timestamp") from error


def _spdx_id(name: str, index: int) -> str:
    normalized = re.sub(r"[^A-Za-z0-9.-]", "-", name)
    return f"SPDXRef-Package-{normalized}-{index}"


def _runtime_packages(root: Path, version: str) -> list[tuple[str, str]]:
    packages = [("kip-knowledge-fabric", version)]
    for line in (root / "requirements/runtime.txt").read_text(encoding="utf-8").splitlines():
        requirement = line.strip()
        if not requirement or requirement.startswith(("#", "--")) or "==" not in requirement:
            continue
        name, package_version = requirement.split("==", maxsplit=1)
        package_version = package_version.split(maxsplit=1)[0].rstrip("\\")
        packages.append((name, package_version))
    return packages


def _sbom(root: Path, version: str, created: datetime) -> dict[str, Any]:
    lock_bytes = (root / "requirements/runtime.txt").read_bytes()
    packages = []
    relationships = []
    root_id = ""
    for index, (name, package_version) in enumerate(
        _runtime_packages(root, version), start=1
    ):
        spdx_id = _spdx_id(f"{name}-{package_version}", index)
        if name == "kip-knowledge-fabric":
            root_id = spdx_id
        packages.append(
            {
                "SPDXID": spdx_id,
                "downloadLocation": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceLocator": f"pkg:pypi/{name}@{package_version}",
                        "referenceType": "purl",
                    }
                ],
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "name": name,
                "versionInfo": package_version,
            }
        )
        if name != "kip-knowledge-fabric":
            relationships.append(
                {
                    "relatedSpdxElement": spdx_id,
                    "relationshipType": "DEPENDS_ON",
                    "spdxElementId": root_id,
                }
            )
    if not root_id:
        raise ReleaseError("uv.lock does not contain the KIP project package")
    namespace_hash = hashlib.sha256(lock_bytes + version.encode()).hexdigest()
    return {
        "SPDXID": "SPDXRef-DOCUMENT",
        "creationInfo": {
            "created": created.isoformat().replace("+00:00", "Z"),
            "creators": ["Tool: kip-release-artifacts/1"],
        },
        "dataLicense": "CC0-1.0",
        "documentNamespace": f"https://kip.local/spdx/{namespace_hash}",
        "name": f"kip-knowledge-fabric-{version}",
        "packages": packages,
        "relationships": [
            {
                "relatedSpdxElement": root_id,
                "relationshipType": "DESCRIBES",
                "spdxElementId": "SPDXRef-DOCUMENT",
            },
            *relationships,
        ],
        "spdxVersion": "SPDX-2.3",
    }


def _material(path: Path, relative: str) -> dict[str, Any]:
    return {
        "digest": {"sha256": _sha256(path)},
        "uri": f"git+file:///{relative}",
    }


def _provenance(
    root: Path,
    wheel: Path,
    images: dict[str, str],
    created: datetime,
    commit: str,
    dirty: bool,
) -> dict[str, Any]:
    subject_digest = _sha256(wheel)
    timestamp = created.isoformat().replace("+00:00", "Z")
    materials = [
        _material(root / relative, relative)
        for relative in ("Dockerfile", "compose.production.yaml", "pyproject.toml", "uv.lock")
    ]
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://kip.local/buildtypes/starter-kit/v1",
                "externalParameters": {"images": images},
                "internalParameters": {"source_tree_dirty": dirty},
                "resolvedDependencies": materials,
            },
            "runDetails": {
                "builder": {"id": "https://kip.local/builders/release-artifacts/v1"},
                "metadata": {
                    "finishedOn": timestamp,
                    "invocationId": f"urn:sha256:{subject_digest}",
                    "startedOn": timestamp,
                },
            },
        },
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [
            {
                "digest": {"sha256": subject_digest},
                "name": wheel.name,
            }
        ],
        "source": {"git_commit": commit},
    }


def _manifest_payload(
    output: Path,
    version: str,
    created: datetime,
    commit: str,
    dirty: bool,
) -> dict[str, Any]:
    files = {
        path.relative_to(output).as_posix(): f"sha256:{_sha256(path)}"
        for path in _files(output)
        if path.name not in {"RELEASE-MANIFEST.json", "SHA256SUMS"}
    }
    return {
        "schema_version": "kip.release-manifest.v1",
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "files": files,
        "source": {"git_commit": commit, "tracked_changes": dirty},
        "version": version,
    }


def _write_checksums(output: Path) -> None:
    entries = [
        f"{_sha256(path)}  {path.relative_to(output).as_posix()}"
        for path in _files(output)
        if path.name != "SHA256SUMS"
    ]
    (output / "SHA256SUMS").write_text("\n".join(entries) + "\n", encoding="utf-8")


def _validate_image(reference: str, name: str) -> None:
    if not IMAGE_PATTERN.fullmatch(reference):
        raise ReleaseError(f"{name} image must be an immutable repository@sha256 reference")


def _path_is_forbidden(relative: PurePosixPath) -> bool:
    lowered = tuple(part.lower() for part in relative.parts)
    return any(part in FORBIDDEN_PARTS for part in lowered) or relative.suffix.lower() in FORBIDDEN_SUFFIXES


def _scan_text(path: Path, relative: str) -> None:
    if path.stat().st_size > 4 * 1024 * 1024:
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return
    for pattern in PRIVATE_PATTERNS:
        if pattern.search(text):
            raise ReleaseError(f"forbidden secret or private path in {relative}")
    for match in DATABASE_URL_PATTERN.finditer(text):
        password = match.group(1)
        if "${" in match.group(0) or password.lower() in SAFE_EXAMPLE_PASSWORDS:
            continue
        raise ReleaseError(f"forbidden database credential in {relative}")


def _parse_checksums(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ReleaseError("SHA256SUMS contains an invalid entry")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or relative in parsed:
            raise ReleaseError("SHA256SUMS contains an unsafe path")
        parsed[relative] = digest
    return parsed


def _verify_wheel(path: Path, expected_version: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            for name in names:
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise ReleaseError("wheel contains an unsafe path")
            metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(metadata_names) != 1:
                raise ReleaseError("wheel metadata is missing")
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
            if metadata.get("Name") != "kip-knowledge-fabric":
                raise ReleaseError("wheel package name does not match KIP")
            if metadata.get("Version") != expected_version:
                raise ReleaseError("wheel version does not match release version")
    except zipfile.BadZipFile as error:
        raise ReleaseError("wheel is not a valid ZIP archive") from error


def verify_bundle(bundle: Path) -> dict[str, Any]:
    if not bundle.is_dir():
        raise ReleaseError("release bundle directory does not exist")
    actual_files = {
        path.relative_to(bundle).as_posix(): path for path in _files(bundle)
    }
    for required in REQUIRED_BUNDLE_FILES:
        if required not in actual_files:
            raise ReleaseError(f"required release artifact is missing: {required}")
    wheels = sorted((bundle / "artifacts/wheels").glob("*.whl"))
    if len(wheels) != 1:
        raise ReleaseError("release bundle must contain exactly one wheel")
    for excluded in STARTER_EXCLUDED_PATHS:
        excluded_relative = f"starter/{excluded}"
        if excluded_relative in actual_files:
            raise ReleaseError(
                f"forbidden private golden corpus in release bundle: {excluded_relative}"
            )
    for relative, path in actual_files.items():
        if _path_is_forbidden(PurePosixPath(relative)):
            raise ReleaseError(f"forbidden release path: {relative}")
        if path.is_symlink():
            raise ReleaseError(f"release bundle contains a symlink: {relative}")
        _scan_text(path, relative)
    checksums = _parse_checksums(bundle / "SHA256SUMS")
    expected_checksum_paths = set(actual_files) - {"SHA256SUMS"}
    if set(checksums) != expected_checksum_paths:
        raise ReleaseError("SHA256SUMS does not cover the complete bundle")
    for relative, digest in checksums.items():
        if _sha256(actual_files[relative]) != digest:
            raise ReleaseError(f"checksum mismatch: {relative}")
    manifest = json.loads((bundle / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "kip.release-manifest.v1":
        raise ReleaseError("release manifest schema is invalid")
    manifest_files = manifest.get("files", {})
    expected_manifest_paths = expected_checksum_paths - {"RELEASE-MANIFEST.json"}
    if set(manifest_files) != expected_manifest_paths:
        raise ReleaseError("release manifest does not cover the payload")
    for relative, digest in manifest_files.items():
        if digest != f"sha256:{_sha256(actual_files[relative])}":
            raise ReleaseError(f"release manifest digest mismatch: {relative}")
    image_lock = json.loads(
        (bundle / "artifacts/images.lock.json").read_text(encoding="utf-8")
    )
    if image_lock.get("schema_version") != "kip.image-lock.v1":
        raise ReleaseError("image lock schema is invalid")
    images = image_lock.get("images", {})
    if set(images) != {"api", "worker", "migrate"}:
        raise ReleaseError("image lock must contain API, worker, and migration images")
    for name, reference in images.items():
        _validate_image(reference, name)
    sbom = json.loads((bundle / "artifacts/sbom.spdx.json").read_text(encoding="utf-8"))
    if sbom.get("spdxVersion") != "SPDX-2.3" or not sbom.get("packages"):
        raise ReleaseError("SPDX SBOM is invalid")
    provenance = json.loads(
        (bundle / "artifacts/provenance.intoto.json").read_text(encoding="utf-8")
    )
    if provenance.get("predicateType") != "https://slsa.dev/provenance/v1":
        raise ReleaseError("provenance predicate is invalid")
    _verify_wheel(wheels[0], str(manifest.get("version", "")))
    subjects = provenance.get("subject", [])
    expected_subject = {"sha256": _sha256(wheels[0])}
    if len(subjects) != 1 or subjects[0].get("digest") != expected_subject:
        raise ReleaseError("provenance does not bind the wheel")
    claude = (bundle / "starter/CLAUDE.md").read_text(encoding="utf-8")
    if "AGENTS.md" not in claude:
        raise ReleaseError("CLAUDE.md no longer imports AGENTS.md")
    return {
        "schema_version": "kip.release-verification.v1",
        "file_count": len(actual_files),
        "status": "verified",
        "version": manifest.get("version"),
    }


def _archive(bundle: Path, epoch: int) -> Path:
    archive_path = bundle.with_name(f"{bundle.name}.tar.gz")
    if archive_path.exists():
        raise ReleaseError("release archive already exists")
    with (
        archive_path.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for path in [bundle, *sorted(bundle.rglob("*"))]:
            relative = path.relative_to(bundle)
            name = "." if not relative.parts else relative.as_posix()
            info = archive.gettarinfo(str(path), arcname=name)
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = epoch
            if info.isfile():
                with path.open("rb") as handle:
                    archive.addfile(info, handle)
            else:
                archive.addfile(info)
    return archive_path


def build_bundle(
    root: Path,
    output: Path,
    wheel: Path,
    images: dict[str, str],
    allow_dirty: bool,
) -> dict[str, Any]:
    root = root.resolve()
    output = output.resolve()
    wheel = wheel.resolve()
    if output.exists():
        raise ReleaseError("release output already exists")
    if not wheel.is_file() or wheel.is_symlink() or wheel.suffix != ".whl":
        raise ReleaseError("release wheel must be a regular .whl file")
    for name, reference in images.items():
        _validate_image(reference, name)
    tracked_changes = bool(_git_value(root, "status", "--porcelain", "--untracked-files=no"))
    if tracked_changes and not allow_dirty:
        raise ReleaseError("tracked source changes must be committed before release")
    created = _release_time()
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    commit = _git_value(root, "rev-parse", "HEAD")
    output.mkdir(parents=True)
    _copy_starter(root, output / "starter")
    wheel_target = output / "artifacts/wheels" / wheel.name
    wheel_target.parent.mkdir(parents=True)
    shutil.copy2(wheel, wheel_target)
    _write_json(
        output / "artifacts/images.lock.json",
        {"schema_version": "kip.image-lock.v1", "images": images},
    )
    _write_json(output / "artifacts/sbom.spdx.json", _sbom(root, version, created))
    _write_json(
        output / "artifacts/provenance.intoto.json",
        _provenance(root, wheel_target, images, created, commit, tracked_changes),
    )
    _write_json(
        output / "RELEASE-MANIFEST.json",
        _manifest_payload(output, version, created, commit, tracked_changes),
    )
    _write_checksums(output)
    verification = verify_bundle(output)
    archive = _archive(output, int(created.timestamp()))
    return {
        "schema_version": "kip.release-build.v1",
        "archive": str(archive),
        "bundle": str(output),
        "verification": verification,
    }


def _verify_archive(path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="kip-release-verify-") as temporary:
        destination = Path(temporary)
        try:
            with tarfile.open(path, "r:gz") as archive:
                for member in archive.getmembers():
                    pure = PurePosixPath(member.name)
                    if pure.is_absolute() or ".." in pure.parts or member.issym() or member.islnk():
                        raise ReleaseError("release archive contains an unsafe path")
                archive.extractall(destination, filter="data")
        except (tarfile.TarError, OSError) as error:
            raise ReleaseError("release archive is invalid") from error
        return verify_bundle(destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--root", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--wheel", type=Path, required=True)
    build.add_argument("--api-image", required=True)
    build.add_argument("--worker-image", required=True)
    build.add_argument("--migrate-image", required=True)
    build.add_argument("--allow-dirty", action="store_true")
    verify = subparsers.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "build":
            payload = build_bundle(
                arguments.root,
                arguments.output,
                arguments.wheel,
                {
                    "api": arguments.api_image,
                    "migrate": arguments.migrate_image,
                    "worker": arguments.worker_image,
                },
                arguments.allow_dirty,
            )
        elif arguments.bundle.is_dir():
            payload = verify_bundle(arguments.bundle.resolve())
        else:
            payload = _verify_archive(arguments.bundle.resolve())
    except (OSError, ReleaseError, ValueError, json.JSONDecodeError) as error:
        print(f"release verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
