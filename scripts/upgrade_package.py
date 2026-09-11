#!/usr/bin/env python3
"""Upgrade a package-based KIP deployment in place from a verified package archive.

Standard library only: it must run against a deployment whose project code is
about to be replaced. The boundary is mechanical (DEPLOYMENT_GUIDE section
11): a path listed in the installed KIP-MANIFEST.json or in the new
archive's manifest is package-owned and is replaced or removed; every other path
belongs to the deployment and is never touched. `.mcp.json` is the one
package-listed file that setup also generates, so it is preserved when present.
Replaced and removed files are archived under var/upgrades/<id>/ for rollback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from unicodedata import normalize

MANIFEST_NAME = "KIP-MANIFEST.json"
LEGACY_MANIFEST_NAME = "STARTER-KIT-MANIFEST.json"  # written by releases before 3.10.0
SCHEMA_VERSIONS = frozenset({"kip.package-archive.v1", "kip.starter-archive.v1"})
CHECKSUM_NAME = "SHA256SUMS"
MAX_ENTRY_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
PRESERVED = frozenset({".mcp.json"})
UPGRADE_ROOT = Path("var/upgrades")


class UpgradeError(RuntimeError):
    pass


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_relative(name: str) -> PurePosixPath:
    # Kits built on macOS may carry NFD names while CI builds carry NFC; the
    # same file must compare equal, so every path is keyed in NFC.
    relative = PurePosixPath(normalize("NFC", name))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise UpgradeError(f"unsafe archive path: {name}")
    return relative


def read_archive(archive: Path) -> tuple[str, dict, dict[str, bytes], dict[str, int]]:
    """Return (version, manifest, payload files, file modes) after verifying digests."""
    try:
        with zipfile.ZipFile(archive) as zipped:
            infos = [info for info in zipped.infolist() if not info.is_dir()]
            roots = {_safe_relative(info.filename).parts[0] for info in infos}
            if len(roots) != 1:
                raise UpgradeError("archive must contain exactly one package directory")
            root = roots.pop()
            files: dict[str, bytes] = {}
            modes: dict[str, int] = {}
            total = 0
            for info in infos:
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise UpgradeError(f"symlinks are not allowed in a package: {info.filename}")
                relative = _safe_relative(info.filename).relative_to(root).as_posix()
                with zipped.open(info) as handle:
                    content = handle.read(MAX_ENTRY_BYTES + 1)
                total += len(content)
                if len(content) > MAX_ENTRY_BYTES or total > MAX_TOTAL_BYTES:
                    raise UpgradeError("archive exceeds the package size limits")
                files[relative] = content
                modes[relative] = (info.external_attr >> 16) & 0o777
    except (OSError, zipfile.BadZipFile) as error:
        raise UpgradeError(f"invalid package archive: {error}") from error
    # The release also publishes a legacy-format copy for 3.9.x upgraders
    # (manifest under its former name); accept it here as well.
    manifest_name = MANIFEST_NAME if MANIFEST_NAME in files else LEGACY_MANIFEST_NAME
    if manifest_name not in files or CHECKSUM_NAME not in files:
        raise UpgradeError("archive lacks its manifest or checksum list")
    manifest = json.loads(files[manifest_name].decode("utf-8"))
    if manifest.get("schema_version") not in SCHEMA_VERSIONS or not isinstance(manifest.get("files"), dict):
        raise UpgradeError("unsupported package manifest")
    if normalize("NFC", str(manifest.get("root"))) != root:
        raise UpgradeError("archive directory does not match its manifest root")
    # Manifest keys drive file writes and deletions, so they get the same
    # containment check as archive entries.
    manifest["files"] = {_safe_relative(name).as_posix(): digest for name, digest in manifest["files"].items()}
    checksums: dict[str, str] = {}
    for line in files[CHECKSUM_NAME].decode("utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise UpgradeError("checksum entry is invalid")
        checksums[normalize("NFC", relative)] = digest
    payload = {name: content for name, content in files.items() if name != CHECKSUM_NAME}
    if set(checksums) != set(payload):
        raise UpgradeError("checksums do not cover the archive payload")
    if set(manifest["files"]) != set(payload) - {manifest_name}:
        raise UpgradeError("manifest does not match the archive payload")
    for name, content in payload.items():
        digest = _sha256(content)
        if checksums[name] != digest:
            raise UpgradeError(f"checksum mismatch: {name}")
        if name != manifest_name and manifest["files"].get(name) != f"sha256:{digest}":
            raise UpgradeError(f"manifest digest mismatch: {name}")
    version = str(manifest.get("version", ""))
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise UpgradeError("manifest version is invalid")
    payload[CHECKSUM_NAME] = files[CHECKSUM_NAME]
    if manifest_name != MANIFEST_NAME:
        # Install the manifest under its current name so the deployment
        # converges on the canonical layout whichever copy was downloaded.
        payload[MANIFEST_NAME] = payload.pop(manifest_name)
        modes[MANIFEST_NAME] = modes.pop(manifest_name)
    return version, manifest, payload, modes


def _version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def load_deployment(deployment: Path) -> tuple[str, dict]:
    if (deployment / ".git").exists():
        raise UpgradeError(f"{deployment} is a git checkout; update it with git pull")
    version_file = deployment / "VERSION"
    manifest_file = deployment / MANIFEST_NAME
    if not manifest_file.is_file() and (deployment / LEGACY_MANIFEST_NAME).is_file():
        manifest_file = deployment / LEGACY_MANIFEST_NAME
    if not version_file.is_file() or not manifest_file.is_file():
        raise UpgradeError(f"{deployment} is not a package-based KIP deployment (VERSION and {MANIFEST_NAME} required)")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(manifest.get("files"), dict):
        raise UpgradeError("installed manifest is unreadable")
    manifest["files"] = {_safe_relative(name).as_posix(): digest for name, digest in manifest["files"].items()}
    version = version_file.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise UpgradeError(f"installed VERSION {version!r} is not X.Y.Z; restore it before upgrading")
    return version, manifest


def plan_upgrade(installed: dict, new_manifest: dict, deployment: Path) -> dict[str, list[str]]:
    old_files = set(installed["files"]) | {MANIFEST_NAME, CHECKSUM_NAME}
    new_files = set(new_manifest["files"]) | {MANIFEST_NAME, CHECKSUM_NAME}
    preserved = sorted(name for name in PRESERVED if name in new_files and (deployment / name).exists())
    replace = sorted(name for name in new_files if name not in preserved)
    remove = sorted(name for name in old_files - new_files if name not in PRESERVED and (deployment / name).exists())
    if (deployment / LEGACY_MANIFEST_NAME).is_file() and LEGACY_MANIFEST_NAME not in new_files:
        # The renamed manifest replaces the legacy file; back it up and drop it.
        remove = sorted([*remove, LEGACY_MANIFEST_NAME])
    added = sorted(name for name in new_files - old_files if name not in preserved)
    return {"replace": replace, "remove": remove, "added": added, "preserved": preserved}


def changelog_between(new_changelog: str, old_version: str, new_version: str) -> str:
    sections = re.split(r"(?m)^## ", new_changelog)
    selected = []
    for section in sections[1:]:
        match = re.match(r"(\d+\.\d+\.\d+)", section)
        if match and _version_key(old_version) < _version_key(match.group(1)) <= _version_key(new_version):
            selected.append("## " + section.rstrip() + "\n")
    return "\n".join(selected)


def apply_upgrade(deployment: Path, archive: Path, *, dry_run: bool) -> int:
    new_version, new_manifest, payload, modes = read_archive(archive)
    old_version, installed = load_deployment(deployment)
    if _version_key(new_version) < _version_key(old_version):
        raise UpgradeError(f"archive {new_version} is older than the installed {old_version}; use --rollback for the last upgrade instead")
    plan = plan_upgrade(installed, new_manifest, deployment)
    notes = changelog_between(payload.get("CHANGELOG.md", b"").decode("utf-8", "replace"), old_version, new_version)
    print(f"Upgrade {old_version} -> {new_version}: replace {len(plan['replace'])} package files, "
          f"remove {len(plan['remove'])}, preserve {len(plan['preserved'])} setup-owned file(s); "
          "deployment-owned paths are untouched.")
    if plan["remove"]:
        print("Removed package files: " + ", ".join(plan["remove"][:20]) + (" …" if len(plan["remove"]) > 20 else ""))
    if notes:
        print("\nChanges since the installed version:\n" + notes)
        if re.search(r"reextract", notes):
            print("NOTE: this range mentions parser re-extraction; after migrating run "
                  "`./scripts/kip parser reextract --source SOURCE` (add `--extension .pdf` for PDF parser "
                  "changes) as a shadow check, then repeat with `--activate`.")
    if dry_run:
        print("Dry run: nothing was changed.")
        return 0

    upgrade_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{old_version}-to-{new_version}"
    try:
        backup_dir = deployment / UPGRADE_ROOT / upgrade_id
        suffix = 1
        while backup_dir.exists():
            suffix += 1
            backup_dir = deployment / UPGRADE_ROOT / f"{upgrade_id}-{suffix}"
        backup_dir.mkdir(parents=True, exist_ok=False)
        with tarfile.open(backup_dir / "previous-package-files.tar.gz", "w:gz") as backup:
            for name in plan["replace"] + plan["remove"]:
                path = deployment / name
                if path.is_file():
                    backup.add(path, arcname=name)
        (backup_dir / "plan.json").write_text(json.dumps({
            "schema_version": "kip.package-upgrade-plan.v1", "from": old_version, "to": new_version,
            "archive_sha256": _sha256(archive.read_bytes()), **plan,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as error:
        raise UpgradeError(f"could not record the rollback point under {UPGRADE_ROOT}: {error}") from error

    # Stage every new file next to its destination, then rename: a failure
    # midway leaves originals in place instead of a half-written tree.
    staged: list[tuple[Path, Path]] = []
    try:
        for name in plan["replace"]:
            destination = deployment / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(prefix=".kip-upgrade-", dir=destination.parent)
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload[name])
            temp_path = Path(temp_name)
            temp_path.chmod((modes.get(name) or 0o644) | stat.S_IRUSR | stat.S_IWUSR)
            staged.append((temp_path, destination))
    except OSError as error:
        for temp_path, _ in staged:
            temp_path.unlink(missing_ok=True)
        raise UpgradeError(f"could not stage new files: {error}") from error
    committed = 0
    try:
        for temp_path, destination in staged:
            os.replace(temp_path, destination)
            committed += 1
        for name in plan["remove"]:
            (deployment / name).unlink(missing_ok=True)
    except OSError as error:
        for temp_path, _ in staged[committed:]:
            temp_path.unlink(missing_ok=True)
        raise UpgradeError(
            f"applying the new package stopped after {committed} of {len(staged)} files ({error}); "
            f"run ./scripts/upgrade.sh --rollback {backup_dir.name} to restore the previous package"
        ) from error
    print(f"Applied KIP {new_version}. Previous package files: {backup_dir.relative_to(deployment)}/")
    return 0


def _upgrade_order(path: Path) -> tuple[str, int]:
    # "<timestamp>-<from>-to-<to>" optionally followed by "-<n>" for repeats.
    match = re.match(r"^(.*?-to-\d+\.\d+\.\d+)(?:-(\d+))?$", path.name)
    return (match.group(1), int(match.group(2) or 1)) if match else (path.name, 1)


def rollback(deployment: Path, upgrade_id: str | None) -> int:
    root = deployment / UPGRADE_ROOT
    candidates = sorted(
        (path for path in root.glob("*") if (path / "plan.json").is_file()), key=_upgrade_order,
    ) if root.is_dir() else []
    if not candidates:
        raise UpgradeError("no recorded upgrade to roll back")
    if upgrade_id and (upgrade_id != Path(upgrade_id).name or upgrade_id in {".", ".."}):
        raise UpgradeError("upgrade id must be a directory name under var/upgrades")
    chosen = root / upgrade_id if upgrade_id else candidates[-1]
    if not (chosen / "plan.json").is_file():
        raise UpgradeError(f"unknown upgrade id: {upgrade_id}")
    try:
        plan = json.loads((chosen / "plan.json").read_text(encoding="utf-8"))
        current = (deployment / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, json.JSONDecodeError) as error:
        raise UpgradeError(f"cannot read the upgrade record or VERSION: {error}") from error
    # A partially applied upgrade still reports the old VERSION; rollback must
    # accept both the target and the source version of that record.
    if current not in {plan["to"], plan["from"]}:
        raise UpgradeError(f"installed version {current} matches neither {plan['from']} nor {plan['to']} of this record; refusing to roll back")
    for name in plan["added"]:
        (deployment / name).unlink(missing_ok=True)
    tar_path = chosen / "previous-package-files.tar.gz"
    if not tar_path.is_file():
        tar_path = chosen / "previous-kit-files.tar.gz"  # records written by 3.9.x
    with tarfile.open(tar_path) as backup:
        for member in backup.getmembers():
            _safe_relative(member.name)
            if not member.isfile():
                raise UpgradeError(f"unexpected backup entry: {member.name}")
        backup.extractall(deployment, filter="data")
    if LEGACY_MANIFEST_NAME in plan["remove"]:
        # This upgrade introduced the renamed manifest; the restored legacy
        # file is the installed manifest again, so the newer one must go or
        # it would shadow it on the next upgrade.
        (deployment / MANIFEST_NAME).unlink(missing_ok=True)
    (chosen / "rolled-back.json").write_text(json.dumps({"at": datetime.now(UTC).isoformat()}) + "\n", encoding="utf-8")
    print(f"Rolled back to KIP {plan['from']} from {chosen.relative_to(deployment)}/. Run ./scripts/bootstrap.sh to resync dependencies.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deployment", type=Path, default=Path.cwd())
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--archive", type=Path, help="verified kip-X.Y.Z.zip")
    group.add_argument("--rollback", nargs="?", const="", metavar="UPGRADE_ID", help="restore the previous package files")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and changelog only")
    args = parser.parse_args()
    deployment = args.deployment.resolve()
    try:
        if args.rollback is not None:
            return rollback(deployment, args.rollback or None)
        return apply_upgrade(deployment, args.archive.resolve(), dry_run=args.dry_run)
    except UpgradeError as error:
        print(f"upgrade: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
