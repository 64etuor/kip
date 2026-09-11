from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _make_kit(directory: Path, version: str, files: dict[str, bytes], executable: set[str] = frozenset()) -> Path:
    """Write kip-starter-kit-<version>.zip plus its .sha256 sidecar the way the builder does."""
    root = f"kip-starter-kit-{version}"
    files = {**files, "VERSION": f"{version}\n".encode()}
    manifest = {
        "schema_version": "kip.starter-archive.v1", "version": version,
        "created_at": "2026-09-11T00:00:00Z", "root": root,
        "files": {name: "sha256:" + hashlib.sha256(content).hexdigest() for name, content in files.items()},
        "source": {"git_commit": "a" * 40, "tracked_changes": False, "repository": "https://example.invalid/kip"},
    }
    manifest_bytes = json.dumps(manifest, indent=2).encode()
    payload = {**files, "STARTER-KIT-MANIFEST.json": manifest_bytes}
    checksums = "".join(f"{hashlib.sha256(c).hexdigest()}  {n}\n" for n, c in sorted(payload.items())).encode()
    payload["SHA256SUMS"] = checksums
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"{root}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, content in sorted(payload.items()):
            info = zipfile.ZipInfo(f"{root}/{name}", date_time=(2026, 9, 11, 0, 0, 0))
            mode = 0o755 if name in executable or name.startswith("scripts/") else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            zipped.writestr(info, content)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (directory / f"{root}.zip.sha256").write_text(f"{digest}  {root}.zip\n")
    return archive


def _release_files(tmp_path: Path, version: str, files: dict[str, bytes], **kwargs) -> tuple[Path, dict[str, str]]:
    releases = tmp_path / "releases"
    archive = _make_kit(releases / f"v{version}", version, files, **kwargs)
    env = {
        **os.environ,
        "KIP_RELEASE_BASE_URL": releases.as_uri(),
        "KIP_VERSION": version,
        "HOME": str(tmp_path / "home"),
    }
    return archive, env


def _kit_scripts() -> dict[str, bytes]:
    return {
        "scripts/upgrade.sh": (ROOT / "scripts/upgrade.sh").read_bytes(),
        "scripts/upgrade_kit.py": (ROOT / "scripts/upgrade_kit.py").read_bytes(),
        "scripts/runtime-path.sh": (ROOT / "scripts/runtime-path.sh").read_bytes(),
        "scripts/install.sh": (ROOT / "scripts/install.sh").read_bytes(),
        "scripts/bootstrap.sh": b"#!/bin/sh\necho bootstrap-stub\n",
    }


def test_installer_verifies_the_sidecar_before_extracting(tmp_path: Path) -> None:
    files = {"README.md": b"# kit\n", "scripts/kip": b"#!/bin/sh\necho kip\n", **_kit_scripts()}
    _, env = _release_files(tmp_path, "9.9.9", files, executable={"scripts/kip"})
    target = tmp_path / "install"

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(target), "--no-bootstrap"],
        env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (target / "VERSION").read_text().strip() == "9.9.9"
    assert (target / "README.md").read_bytes() == b"# kit\n"
    assert os.access(target / "scripts/kip", os.X_OK)
    assert "Verified kip-starter-kit-9.9.9.zip" in result.stderr


def test_installer_refuses_a_tampered_archive_without_extracting(tmp_path: Path) -> None:
    archive, env = _release_files(tmp_path, "9.9.9", {"README.md": b"# kit\n", **_kit_scripts()})
    archive.write_bytes(archive.read_bytes() + b"tamper")
    target = tmp_path / "install"

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(target), "--no-bootstrap"],
        env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr and "nothing was extracted" in result.stderr
    assert not target.exists()


def test_installer_resolves_latest_release_and_refuses_non_empty_targets(tmp_path: Path) -> None:
    _, env = _release_files(tmp_path, "9.9.9", {"README.md": b"# kit\n", **_kit_scripts()})
    env.pop("KIP_VERSION")
    latest = tmp_path / "latest.json"
    latest.write_text(json.dumps({"tag_name": "v9.9.9", "name": "KIP 9.9.9"}))
    env["KIP_LATEST_URL"] = latest.as_uri()
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "notes.txt").write_text("mine")

    refused = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(occupied), "--no-bootstrap"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert refused.returncode == 1 and "not empty" in refused.stderr
    assert (occupied / "notes.txt").read_text() == "mine"

    fresh = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(tmp_path / "fresh"), "--no-bootstrap"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert fresh.returncode == 0, fresh.stderr
    assert "Downloading KIP 9.9.9" in fresh.stderr


def _deploy(tmp_path: Path, version: str, kit_files: dict[str, bytes]) -> Path:
    archive = _make_kit(tmp_path / "kits", version, kit_files)
    deployment = tmp_path / "deployment"
    with zipfile.ZipFile(archive) as zipped:
        for info in zipped.infolist():
            relative = Path(*Path(info.filename).parts[1:])
            destination = deployment / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(zipped.read(info))
            destination.chmod((info.external_attr >> 16) & 0o777 or 0o644)
    # Deployment-owned state that an upgrade must never touch.
    (deployment / ".env").write_text("KIP_DATABASE_URL=postgresql://kip_owner:test-password@127.0.0.1:5432/kip\n")
    (deployment / "config/kip.toml").write_text("[app]\nworkspace = 'acme'\n")
    (deployment / ".mcp.json").write_text('{"mcpServers": {"kip": {"env": {"KIP_CONFIG": "config/kip.host.generated.toml"}}}}\n')
    (deployment / "var/cas").mkdir(parents=True)
    (deployment / "var/cas/blob").write_bytes(b"blob")
    (deployment / "ontology/domains/local.yaml").parent.mkdir(parents=True, exist_ok=True)
    (deployment / "ontology/domains/local.yaml").write_text("domain: local\n")
    return deployment


OLD_KIT = {
    "README.md": b"old readme\n", "CHANGELOG.md": b"# Changelog\n\n## 1.0.0 - 2026-01-01\n\n- first\n",
    "src/kip/__init__.py": b"__version__ = '1.0.0'\n", "src/kip/legacy.py": b"legacy\n",
    "config/kip.example.toml": b"[app]\n", ".mcp.json": b'{"mcpServers": {"kip": {"env": {"KIP_CONFIG": "config/kip.toml"}}}}\n',
    "ontology/domains/base.yaml": b"domain: base v1\n", **_kit_scripts(),
}
NEW_KIT = {
    "README.md": b"new readme\n",
    "CHANGELOG.md": b"# Changelog\n\n## 1.1.0 - 2026-02-01\n\n- second; run parser reextract\n\n## 1.0.0 - 2026-01-01\n\n- first\n",
    "src/kip/__init__.py": b"__version__ = '1.1.0'\n", "src/kip/new_module.py": b"new\n",
    "config/kip.example.toml": b"[app]\n# new\n", ".mcp.json": b'{"mcpServers": {"kip": {"env": {"KIP_CONFIG": "config/kip.toml"}}}}\n',
    "ontology/domains/base.yaml": b"domain: base v2\n", **_kit_scripts(),
}


def test_upgrade_replaces_kit_files_and_preserves_deployment_state(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    new_archive = _make_kit(tmp_path / "kits", "1.1.0", NEW_KIT)

    dry = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_kit.py"), "--deployment", str(deployment), "--archive", str(new_archive), "--dry-run"],
        capture_output=True, text=True, check=False,
    )
    assert dry.returncode == 0, dry.stderr
    assert "1.0.0 -> 1.1.0" in dry.stdout and "## 1.1.0" in dry.stdout and "## 1.0.0" not in dry.stdout
    assert "reextract" in dry.stdout
    assert (deployment / "README.md").read_bytes() == b"old readme\n"

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_kit.py"), "--deployment", str(deployment), "--archive", str(new_archive)],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (deployment / "VERSION").read_text().strip() == "1.1.0"
    assert (deployment / "README.md").read_bytes() == b"new readme\n"
    assert (deployment / "src/kip/new_module.py").exists()
    assert not (deployment / "src/kip/legacy.py").exists()
    assert (deployment / "ontology/domains/base.yaml").read_bytes() == b"domain: base v2\n"
    # Deployment-owned and setup-generated paths survive untouched.
    assert (deployment / "ontology/domains/local.yaml").read_text() == "domain: local\n"
    assert "acme" in (deployment / "config/kip.toml").read_text()
    assert "test-password" in (deployment / ".env").read_text()
    assert "kip.host.generated.toml" in (deployment / ".mcp.json").read_text()
    assert (deployment / "var/cas/blob").read_bytes() == b"blob"
    manifest = json.loads((deployment / "STARTER-KIT-MANIFEST.json").read_text())
    assert manifest["version"] == "1.1.0"
    backups = list((deployment / "var/upgrades").glob("*-1.0.0-to-1.1.0"))
    assert len(backups) == 1 and (backups[0] / "previous-kit-files.tar.gz").exists()
    assert not list(deployment.rglob(".kip-upgrade-*"))

    rollback = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_kit.py"), "--deployment", str(deployment), "--rollback"],
        capture_output=True, text=True, check=False,
    )
    assert rollback.returncode == 0, rollback.stderr
    assert (deployment / "VERSION").read_text().strip() == "1.0.0"
    assert (deployment / "README.md").read_bytes() == b"old readme\n"
    assert (deployment / "src/kip/legacy.py").exists()
    assert not (deployment / "src/kip/new_module.py").exists()
    assert "kip.host.generated.toml" in (deployment / ".mcp.json").read_text()


def test_upgrade_refuses_git_checkouts_downgrades_and_bad_archives(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.1.0", NEW_KIT)
    older = _make_kit(tmp_path / "kits", "1.0.0", OLD_KIT)
    downgrade = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_kit.py"), "--deployment", str(deployment), "--archive", str(older)],
        capture_output=True, text=True, check=False,
    )
    assert downgrade.returncode == 1 and "older than the installed" in downgrade.stderr

    (deployment / ".git").mkdir()
    git = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_kit.py"), "--deployment", str(deployment), "--archive", str(older)],
        capture_output=True, text=True, check=False,
    )
    assert git.returncode == 1 and "git pull" in git.stderr


def test_upgrade_rejects_archives_whose_digests_do_not_match(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    archive = _make_kit(tmp_path / "kits", "1.1.0", NEW_KIT)
    forged = tmp_path / "kits" / "forged.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(forged, "w") as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename.endswith("/README.md"):
                content = b"forged\n"
            target.writestr(info, content)

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_kit.py"), "--deployment", str(deployment), "--archive", str(forged)],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 1 and "checksum mismatch" in result.stderr
    assert (deployment / "README.md").read_bytes() == b"old readme\n"


def test_installer_upgrades_an_existing_kit_deployment_in_place(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    _, env = _release_files(tmp_path, "1.1.0", NEW_KIT)

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(deployment), "--no-bootstrap"],
        env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Upgrading 1.0.0 -> 1.1.0" in result.stderr
    assert (deployment / "VERSION").read_text().strip() == "1.1.0"
    assert "acme" in (deployment / "config/kip.toml").read_text()

    again = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(deployment), "--no-bootstrap"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert again.returncode == 0 and "already installed" in again.stderr


def test_upgrade_dry_run_through_the_installer_changes_nothing(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    _, env = _release_files(tmp_path, "1.1.0", NEW_KIT)

    result = subprocess.run(
        ["/bin/bash", str(deployment / "scripts/upgrade.sh"), "--version", "1.1.0", "--dry-run"],
        env=env, cwd=deployment, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Dry run: nothing was changed." in result.stdout
    assert (deployment / "VERSION").read_text().strip() == "1.0.0"
    fresh = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(tmp_path / "fresh"), "--dry-run"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert fresh.returncode == 1 and "applies to upgrades" in fresh.stderr


@pytest.mark.parametrize("script", ["install.sh", "upgrade.sh"])
def test_installer_scripts_are_standalone_and_executable(script: str) -> None:
    path = ROOT / "scripts" / script
    assert os.access(path, os.X_OK)
    assert "common.sh" not in path.read_text(), "installer must not depend on the project environment"


def test_upgrade_treats_nfd_and_nfc_kit_paths_as_the_same_file(tmp_path: Path) -> None:
    from unicodedata import normalize

    nfc_name = normalize("NFC", "sample-data/정산_안내.txt")
    nfd_name = normalize("NFD", nfc_name)
    deployment = _deploy(tmp_path, "1.0.0", {**OLD_KIT, nfc_name: b"old sample\n"})
    new_archive = _make_kit(tmp_path / "kits", "1.1.0", {**NEW_KIT, nfd_name: b"new sample\n"})

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_kit.py"), "--deployment", str(deployment), "--archive", str(new_archive), "--dry-run"],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    # Only the genuinely dropped legacy module is removed; the sample file is
    # recognised as the same path despite the NFD spelling in the new kit.
    assert "remove 1," in result.stdout
    assert "Removed kit files: src/kip/legacy.py" in result.stdout


def _rewrite_kit(archive: Path, mutate) -> Path:
    """Rebuild a kit zip with a mutated manifest and consistent SHA256SUMS (payload digests untouched)."""
    with zipfile.ZipFile(archive) as source:
        root = source.infolist()[0].filename.split("/")[0]
        entries = {info.filename.split("/", 1)[1]: (info, source.read(info)) for info in source.infolist() if not info.is_dir()}
    manifest = json.loads(entries["STARTER-KIT-MANIFEST.json"][1])
    mutate(manifest)
    entries["STARTER-KIT-MANIFEST.json"] = (entries["STARTER-KIT-MANIFEST.json"][0], json.dumps(manifest, indent=2).encode())
    checksums = "".join(
        f"{hashlib.sha256(content).hexdigest()}  {name}\n"
        for name, (_, content) in sorted(entries.items()) if name != "SHA256SUMS"
    ).encode()
    entries["SHA256SUMS"] = (entries["SHA256SUMS"][0], checksums)
    rewritten = archive.with_name("mutated-" + archive.name)
    with zipfile.ZipFile(rewritten, "w", zipfile.ZIP_DEFLATED) as target:
        for name, (info, content) in sorted(entries.items()):
            new_info = zipfile.ZipInfo(f"{root}/{name}", date_time=info.date_time)
            new_info.external_attr = info.external_attr
            target.writestr(new_info, content)
    return rewritten


def _run_upgrade(deployment: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_kit.py"), "--deployment", str(deployment), *args],
        capture_output=True, text=True, check=False,
    )


def test_upgrade_rejects_manifest_paths_that_escape_the_deployment(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.txt").write_text("keep me")
    installed = deployment / "STARTER-KIT-MANIFEST.json"
    manifest = json.loads(installed.read_text())
    manifest["files"]["../outside/victim.txt"] = "sha256:" + "0" * 64
    manifest["files"][str(outside / "victim.txt")] = "sha256:" + "0" * 64
    installed.write_text(json.dumps(manifest))
    new_archive = _make_kit(tmp_path / "kits", "1.1.0", NEW_KIT)

    result = _run_upgrade(deployment, "--archive", str(new_archive))

    assert result.returncode == 1 and "unsafe archive path" in result.stderr
    assert (outside / "victim.txt").read_text() == "keep me"
    assert (deployment / "VERSION").read_text().strip() == "1.0.0"


def test_upgrade_rejects_a_manifest_that_lists_files_absent_from_the_archive(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    archive = _make_kit(tmp_path / "kits", "1.1.0", NEW_KIT)

    def add_ghost(manifest):
        manifest["files"]["scripts/ghost.sh"] = "sha256:" + "0" * 64

    mutated = _rewrite_kit(archive, add_ghost)
    result = _run_upgrade(deployment, "--archive", str(mutated))

    assert result.returncode == 1 and "manifest does not match the archive payload" in result.stderr
    assert not list(deployment.rglob(".kip-upgrade-*"))
    assert not (deployment / "var/upgrades").exists()


def test_upgrade_reports_malformed_versions_and_unsafe_rollback_ids(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    archive = _make_kit(tmp_path / "kits", "1.1.0", NEW_KIT)
    (deployment / "VERSION").write_text("3.7.0-dev\n")

    malformed = _run_upgrade(deployment, "--archive", str(archive))
    assert malformed.returncode == 1 and "is not X.Y.Z" in malformed.stderr
    assert "Traceback" not in malformed.stderr

    (deployment / "VERSION").write_text("1.0.0\n")
    assert _run_upgrade(deployment, "--archive", str(archive)).returncode == 0
    escape = _run_upgrade(deployment, "--rollback", "../../outside")
    assert escape.returncode == 1 and "directory name under var/upgrades" in escape.stderr


def test_upgrade_keeps_executable_bits_and_writes_mcp_when_absent(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    (deployment / ".mcp.json").unlink()
    archive = _make_kit(tmp_path / "kits", "1.1.0", NEW_KIT)

    assert _run_upgrade(deployment, "--archive", str(archive)).returncode == 0
    assert os.access(deployment / "scripts/upgrade.sh", os.X_OK)
    assert (deployment / ".mcp.json").read_text() == NEW_KIT[".mcp.json"].decode()


def test_installer_handles_spaces_keep_archive_and_interrupted_targets(tmp_path: Path) -> None:
    _, env = _release_files(tmp_path, "9.9.9", {"README.md": b"# kit\n", **_kit_scripts()})
    target = tmp_path / "my kip dir"

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(target), "--no-bootstrap", "--keep-archive"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (target / "README.md").exists()
    assert (tmp_path / "kip-starter-kit-9.9.9.zip").exists() and (tmp_path / "kip-starter-kit-9.9.9.zip.sha256").exists()

    interrupted = tmp_path / "interrupted"
    interrupted.mkdir()
    (interrupted / "VERSION").write_text("9.9.9\n")
    (interrupted / "STARTER-KIT-MANIFEST.json").write_text("{}")
    broken = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(interrupted), "--no-bootstrap"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert broken.returncode == 1 and "interrupted install" in broken.stderr


def test_rollback_accepts_a_partially_applied_upgrade(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    archive = _make_kit(tmp_path / "kits", "1.1.0", NEW_KIT)
    assert _run_upgrade(deployment, "--archive", str(archive)).returncode == 0
    # Simulate a commit that stopped before VERSION was replaced.
    (deployment / "VERSION").write_text("1.0.0\n")

    result = _run_upgrade(deployment, "--rollback")

    assert result.returncode == 0, result.stderr
    assert (deployment / "README.md").read_bytes() == b"old readme\n"
    (deployment / "VERSION").write_text("9.9.9\n")
    assert "matches neither" in _run_upgrade(deployment, "--rollback").stderr


def test_installer_keeps_a_user_created_empty_directory_on_failure(tmp_path: Path) -> None:
    files = {"README.md": b"# kit\n", **_kit_scripts()}
    del files["scripts/bootstrap.sh"]  # layout check fails after verification
    _, env = _release_files(tmp_path, "9.9.9", files)
    target = tmp_path / "prepared"
    target.mkdir()

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(target), "--no-bootstrap"],
        env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 1 and "single kit directory" in result.stderr
    assert target.is_dir() and not list(target.iterdir())
