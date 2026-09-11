#!/usr/bin/env python3
"""Write the legacy-format copy of a package archive for 3.9.x upgraders.

Deployments installed from 3.9.x run their own ``scripts/upgrade_kit.py`` when
``kip update`` / ``./scripts/upgrade.sh --latest`` is used. That upgrader
downloads ``kip-starter-kit-<version>.zip`` and requires the manifest to be
named ``STARTER-KIT-MANIFEST.json`` with ``schema_version``
``kip.starter-archive.v1``. This script produces exactly that from the
canonical ``kip-<version>.zip``: the payload files are copied unchanged, the
manifest is renamed with the legacy schema identifier, and ``SHA256SUMS`` is
rewritten for the renamed entry. Everything else (entry order, timestamps,
modes) is preserved so the output is deterministic.

The current upgrader accepts the legacy manifest name and schema identifier,
so a deployment upgraded through this copy keeps upgrading with later releases.
Stdlib only: it runs in CI and in bare release environments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

MANIFEST_NAME = "KIP-MANIFEST.json"
LEGACY_MANIFEST_NAME = "STARTER-KIT-MANIFEST.json"
LEGACY_SCHEMA_VERSION = "kip.starter-archive.v1"
CHECKSUM_NAME = "SHA256SUMS"


class LegacyArchiveError(Exception):
    pass


def _entry(info: zipfile.ZipInfo, filename: str) -> zipfile.ZipInfo:
    entry = zipfile.ZipInfo(filename, date_time=info.date_time)
    entry.compress_type = info.compress_type
    entry.external_attr = info.external_attr
    entry.create_system = info.create_system
    return entry


def write_legacy_archive(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source) as zipped:
        infos = zipped.infolist()
        roots = {info.filename.split("/", 1)[0] for info in infos}
        if len(roots) != 1:
            raise LegacyArchiveError("archive must contain exactly one package directory")
        root = roots.pop()
        manifest_path = f"{root}/{MANIFEST_NAME}"
        checksum_path = f"{root}/{CHECKSUM_NAME}"
        names = {info.filename for info in infos}
        if manifest_path not in names or checksum_path not in names:
            raise LegacyArchiveError(f"archive lacks {MANIFEST_NAME} or {CHECKSUM_NAME}")
        if f"{root}/{LEGACY_MANIFEST_NAME}" in names:
            raise LegacyArchiveError("archive already carries a legacy manifest")
        manifest = json.loads(zipped.read(manifest_path).decode("utf-8"))
        if not isinstance(manifest.get("files"), dict):
            raise LegacyArchiveError("manifest has no files map")
        manifest["schema_version"] = LEGACY_SCHEMA_VERSION
        manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        lines = []
        for line in zipped.read(checksum_path).decode("utf-8").splitlines():
            digest, separator, relative = line.partition("  ")
            if not separator:
                raise LegacyArchiveError("checksum entry is invalid")
            if relative == MANIFEST_NAME:
                continue
            lines.append((relative, digest))
        lines.append((LEGACY_MANIFEST_NAME, hashlib.sha256(manifest_bytes).hexdigest()))
        checksum_bytes = "".join(f"{digest}  {relative}\n" for relative, digest in sorted(lines)).encode("utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged = destination.with_name(destination.name + ".tmp")
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for info in infos:
                if info.filename == manifest_path:
                    output.writestr(_entry(info, f"{root}/{LEGACY_MANIFEST_NAME}"), manifest_bytes)
                elif info.filename == checksum_path:
                    output.writestr(_entry(info, checksum_path), checksum_bytes)
                else:
                    output.writestr(_entry(info, info.filename), zipped.read(info))
        staged.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="canonical kip-<version>.zip")
    parser.add_argument("destination", type=Path, help="kip-starter-kit-<version>.zip to write")
    args = parser.parse_args()
    try:
        write_legacy_archive(args.source, args.destination)
    except (LegacyArchiveError, OSError, zipfile.BadZipFile, ValueError) as error:
        print(f"legacy-archive: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
