from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from kip.domain.starter_archive import StarterArchiveManifest
from kip.errors import ValidationError
from kip.starter_archive import (
    StarterArchiveBuildOptions,
    _normalize_repository,
    build_starter_archive,
    verify_starter_archive,
)

ROOT = Path(__file__).resolve().parents[1]


def test_starter_archive_manifest_contract_is_generated() -> None:
    contract = ROOT / "contracts/starter-archive-manifest.schema.json"

    assert contract.is_file()


def test_starter_archive_manifest_rejects_unknown_fields() -> None:
    payload = {
        "schema_version": "kip.starter-archive.v1",
        "version": "3.4.0",
        "created_at": "2026-08-17T00:00:00Z",
        "root": "kip-starter-kit-3.4.0",
        "files": {"README.md": "sha256:" + "1" * 64},
        "source": {"git_commit": "a" * 40, "tracked_changes": False},
        "unexpected": True,
    }

    with pytest.raises(PydanticValidationError):
        StarterArchiveManifest.model_validate(payload)


def test_starter_archive_cli_builds_a_versioned_zip(tmp_path: Path) -> None:
    output = tmp_path / "kip-starter-kit.zip"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kip.starter_archive_cli",
            "build",
            "--root",
            str(ROOT),
            "--output",
            str(output),
            "--allow-dirty",
            "--source-date-epoch",
            "1786924800",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    assert envelope["schema_version"] == "kip.envelope.v1"
    assert envelope["ok"] is True
    assert envelope["data"]["schema_version"] == "kip.starter-archive-receipt.v1"
    assert output.is_file()


def test_starter_archive_verifier_rejects_a_mismatched_external_digest(
    tmp_path: Path,
) -> None:
    output = tmp_path / "kip-starter-kit.zip"
    build_starter_archive(
        StarterArchiveBuildOptions(
            root=ROOT,
            output=output,
            allow_dirty=True,
            source_date_epoch=1786924800,
        )
    )
    output.with_name(f"{output.name}.sha256").write_text(
        f"{'0' * 64}  {output.name}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="external digest"):
        verify_starter_archive(output)


def test_starter_archive_verifier_rejects_a_manifested_nonessential_file(
    tmp_path: Path,
) -> None:
    output = tmp_path / "kip-starter-kit.zip"
    build_starter_archive(
        StarterArchiveBuildOptions(
            root=ROOT,
            output=output,
            allow_dirty=True,
            source_date_epoch=1786924800,
        )
    )
    with zipfile.ZipFile(output) as archive:
        entries = {info.filename: archive.read(info) for info in archive.infolist()}
    root = next(iter(entries)).split("/", maxsplit=1)[0]
    relative = "docs/plans/internal-only.md"
    entries[f"{root}/{relative}"] = b"internal plan\n"
    manifest_name = f"{root}/STARTER-KIT-MANIFEST.json"
    manifest = json.loads(entries[manifest_name])
    manifest["files"][relative] = "sha256:" + hashlib.sha256(
        entries[f"{root}/{relative}"]
    ).hexdigest()
    entries[manifest_name] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    checksum_name = f"{root}/SHA256SUMS"
    checksum_lines = [
        f"{hashlib.sha256(content).hexdigest()}  {name.split('/', maxsplit=1)[1]}"
        for name, content in sorted(entries.items())
        if name != checksum_name
    ]
    entries[checksum_name] = ("\n".join(checksum_lines) + "\n").encode()
    output.unlink()
    output.with_name(f"{output.name}.sha256").unlink()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            archive.writestr(name, content)

    with pytest.raises(ValidationError, match="nonessential"):
        verify_starter_archive(output)


def test_starter_archive_preserves_executable_script_mode(tmp_path: Path) -> None:
    output = tmp_path / "kip-starter-kit.zip"
    receipt = build_starter_archive(
        StarterArchiveBuildOptions(
            root=ROOT,
            output=output,
            allow_dirty=True,
            source_date_epoch=1786924800,
        )
    )

    with zipfile.ZipFile(output) as archive:
        info = archive.getinfo(f"{receipt.root}/scripts/bootstrap.sh")
    mode = info.external_attr >> 16
    assert stat.S_ISREG(mode)
    assert mode & stat.S_IXUSR


def test_starter_archive_console_entrypoint_is_installed() -> None:
    result = subprocess.run(
        ["uv", "run", "--frozen", "kip-starter-kit", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Build and verify" in result.stdout


def test_starter_archive_cli_wraps_invalid_source_errors(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kip.starter_archive_cli",
            "build",
            "--root",
            str(tmp_path),
            "--output",
            str(tmp_path / "starter.zip"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 3
    envelope = json.loads(result.stderr)
    assert envelope["schema_version"] == "kip.envelope.v1"
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "validation_error"


def test_starter_archive_is_deterministic_and_excludes_local_material(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    options = {
        "root": ROOT,
        "allow_dirty": True,
        "source_date_epoch": 1786924800,
    }

    build_starter_archive(StarterArchiveBuildOptions(output=first, **options))
    build_starter_archive(StarterArchiveBuildOptions(output=second, **options))

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        relative_names = {
            name.split("/", maxsplit=1)[1] for name in archive.namelist()
        }
    assert "src/kip/starter_archive.py" in relative_names
    assert "docs/adr/ADR-052-verified-online-source-zip.md" in relative_names
    assert "config/kip.toml" not in relative_names
    assert "docs/plans/2026-08-17-online-zip-starter-kit.md" not in relative_names
    assert "evaluation/golden/private-onedrive-nl.yaml" not in relative_names
    assert not any(".egg-info/" in name for name in relative_names)


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/acme/kip.git", "https://github.com/acme/kip"),
        ("https://github.com/acme/kip", "https://github.com/acme/kip"),
        ("git@github.com:acme/kip.git", "https://github.com/acme/kip"),
        ("ssh://git@github.com/acme/kip.git", "https://github.com/acme/kip"),
        ("https://github.com:8443/acme/kip", "https://github.com:8443/acme/kip"),
        ("/srv/git/kip", None),
        ("file:///srv/git/kip.git", None),
        ("", None),
    ],
)
def test_starter_archive_normalizes_a_remote_into_shareable_provenance(
    remote: str,
    expected: str | None,
) -> None:
    assert _normalize_repository(remote) == expected


def test_starter_archive_repository_drops_remote_credentials() -> None:
    """A remote can carry an access token; the manifest is handed to others."""
    normalized = _normalize_repository(
        "https://x-access-token:ghp_examplesecret@github.com/acme/kip.git"
    )

    assert normalized == "https://github.com/acme/kip"
    assert "ghp_examplesecret" not in (normalized or "")
    assert "@" not in (normalized or "")


def test_starter_archive_manifest_records_the_source_repository(
    tmp_path: Path,
) -> None:
    output = tmp_path / "kip-starter-kit.zip"

    build_starter_archive(
        StarterArchiveBuildOptions(
            root=ROOT,
            output=output,
            allow_dirty=True,
            source_date_epoch=1786924800,
            repository="git@github.com:acme/kip.git",
        )
    )

    with zipfile.ZipFile(output) as archive:
        name = next(n for n in archive.namelist() if n.endswith("STARTER-KIT-MANIFEST.json"))
        manifest = json.loads(archive.read(name))

    assert manifest["source"]["repository"] == "https://github.com/acme/kip"
