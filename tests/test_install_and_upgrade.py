from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _make_kit(directory: Path, version: str, files: dict[str, bytes], executable: set[str] = frozenset()) -> Path:
    """Write kip-<version>.zip plus its .sha256 sidecar the way the builder does."""
    root = f"kip-{version}"
    files = {**files, "VERSION": f"{version}\n".encode()}
    manifest = {
        "schema_version": "kip.package-archive.v1", "version": version,
        "created_at": "2026-09-11T00:00:00Z", "root": root,
        "files": {name: "sha256:" + hashlib.sha256(content).hexdigest() for name, content in files.items()},
        "source": {"git_commit": "a" * 40, "tracked_changes": False, "repository": "https://example.invalid/kip"},
    }
    manifest_bytes = json.dumps(manifest, indent=2).encode()
    payload = {**files, "KIP-MANIFEST.json": manifest_bytes}
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
        "scripts/upgrade_package.py": (ROOT / "scripts/upgrade_package.py").read_bytes(),
        "scripts/runtime-path.sh": (ROOT / "scripts/runtime-path.sh").read_bytes(),
        "scripts/install.sh": (ROOT / "scripts/install.sh").read_bytes(),
        "scripts/bootstrap.sh": b"#!/bin/sh\necho bootstrap-stub\n",
    }


def test_installer_verifies_the_sidecar_before_extracting(tmp_path: Path) -> None:
    files = {"README.md": b"# package\n", "scripts/kip": b"#!/bin/sh\necho kip\n", **_kit_scripts()}
    _, env = _release_files(tmp_path, "9.9.9", files, executable={"scripts/kip"})
    target = tmp_path / "install"

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(target), "--no-bootstrap"],
        env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (target / "VERSION").read_text().strip() == "9.9.9"
    assert (target / "README.md").read_bytes() == b"# package\n"
    assert os.access(target / "scripts/kip", os.X_OK)
    assert "Verified kip-9.9.9.zip" in result.stderr


def test_installer_refuses_a_tampered_archive_without_extracting(tmp_path: Path) -> None:
    archive, env = _release_files(tmp_path, "9.9.9", {"README.md": b"# package\n", **_kit_scripts()})
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
    _, env = _release_files(tmp_path, "9.9.9", {"README.md": b"# package\n", **_kit_scripts()})
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
    archive = _make_kit(tmp_path / "packages", version, kit_files)
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
    new_archive = _make_kit(tmp_path / "packages", "1.1.0", NEW_KIT)

    dry = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_package.py"), "--deployment", str(deployment), "--archive", str(new_archive), "--dry-run"],
        capture_output=True, text=True, check=False,
    )
    assert dry.returncode == 0, dry.stderr
    assert "1.0.0 -> 1.1.0" in dry.stdout and "## 1.1.0" in dry.stdout and "## 1.0.0" not in dry.stdout
    assert "reextract" in dry.stdout
    assert (deployment / "README.md").read_bytes() == b"old readme\n"

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_package.py"), "--deployment", str(deployment), "--archive", str(new_archive)],
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
    manifest = json.loads((deployment / "KIP-MANIFEST.json").read_text())
    assert manifest["version"] == "1.1.0"
    backups = list((deployment / "var/upgrades").glob("*-1.0.0-to-1.1.0"))
    assert len(backups) == 1 and (backups[0] / "previous-package-files.tar.gz").exists()
    assert not list(deployment.rglob(".kip-upgrade-*"))

    rollback = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_package.py"), "--deployment", str(deployment), "--rollback"],
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
    older = _make_kit(tmp_path / "packages", "1.0.0", OLD_KIT)
    downgrade = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_package.py"), "--deployment", str(deployment), "--archive", str(older)],
        capture_output=True, text=True, check=False,
    )
    assert downgrade.returncode == 1 and "older than the installed" in downgrade.stderr

    (deployment / ".git").mkdir()
    git = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_package.py"), "--deployment", str(deployment), "--archive", str(older)],
        capture_output=True, text=True, check=False,
    )
    assert git.returncode == 1 and "git pull" in git.stderr


def test_upgrade_rejects_archives_whose_digests_do_not_match(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    archive = _make_kit(tmp_path / "packages", "1.1.0", NEW_KIT)
    forged = tmp_path / "packages" / "forged.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(forged, "w") as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename.endswith("/README.md"):
                content = b"forged\n"
            target.writestr(info, content)

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_package.py"), "--deployment", str(deployment), "--archive", str(forged)],
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
    new_archive = _make_kit(tmp_path / "packages", "1.1.0", {**NEW_KIT, nfd_name: b"new sample\n"})

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/upgrade_package.py"), "--deployment", str(deployment), "--archive", str(new_archive), "--dry-run"],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    # Only the genuinely dropped legacy module is removed; the sample file is
    # recognised as the same path despite the NFD spelling in the new package.
    assert "remove 1," in result.stdout
    assert "Removed package files: src/kip/legacy.py" in result.stdout


def _rewrite_kit(archive: Path, mutate) -> Path:
    """Rebuild a package zip with a mutated manifest and consistent SHA256SUMS (payload digests untouched)."""
    with zipfile.ZipFile(archive) as source:
        root = source.infolist()[0].filename.split("/")[0]
        entries = {info.filename.split("/", 1)[1]: (info, source.read(info)) for info in source.infolist() if not info.is_dir()}
    manifest = json.loads(entries["KIP-MANIFEST.json"][1])
    mutate(manifest)
    entries["KIP-MANIFEST.json"] = (entries["KIP-MANIFEST.json"][0], json.dumps(manifest, indent=2).encode())
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
        [sys.executable, str(ROOT / "scripts/upgrade_package.py"), "--deployment", str(deployment), *args],
        capture_output=True, text=True, check=False,
    )


def test_upgrade_rejects_manifest_paths_that_escape_the_deployment(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.txt").write_text("keep me")
    installed = deployment / "KIP-MANIFEST.json"
    manifest = json.loads(installed.read_text())
    manifest["files"]["../outside/victim.txt"] = "sha256:" + "0" * 64
    manifest["files"][str(outside / "victim.txt")] = "sha256:" + "0" * 64
    installed.write_text(json.dumps(manifest))
    new_archive = _make_kit(tmp_path / "packages", "1.1.0", NEW_KIT)

    result = _run_upgrade(deployment, "--archive", str(new_archive))

    assert result.returncode == 1 and "unsafe archive path" in result.stderr
    assert (outside / "victim.txt").read_text() == "keep me"
    assert (deployment / "VERSION").read_text().strip() == "1.0.0"


def test_upgrade_rejects_a_manifest_that_lists_files_absent_from_the_archive(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    archive = _make_kit(tmp_path / "packages", "1.1.0", NEW_KIT)

    def add_ghost(manifest):
        manifest["files"]["scripts/ghost.sh"] = "sha256:" + "0" * 64

    mutated = _rewrite_kit(archive, add_ghost)
    result = _run_upgrade(deployment, "--archive", str(mutated))

    assert result.returncode == 1 and "manifest does not match the archive payload" in result.stderr
    assert not list(deployment.rglob(".kip-upgrade-*"))
    assert not (deployment / "var/upgrades").exists()


def test_upgrade_reports_malformed_versions_and_unsafe_rollback_ids(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    archive = _make_kit(tmp_path / "packages", "1.1.0", NEW_KIT)
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
    archive = _make_kit(tmp_path / "packages", "1.1.0", NEW_KIT)

    assert _run_upgrade(deployment, "--archive", str(archive)).returncode == 0
    assert os.access(deployment / "scripts/upgrade.sh", os.X_OK)
    assert (deployment / ".mcp.json").read_text() == NEW_KIT[".mcp.json"].decode()


def test_installer_handles_spaces_keep_archive_and_interrupted_targets(tmp_path: Path) -> None:
    _, env = _release_files(tmp_path, "9.9.9", {"README.md": b"# package\n", **_kit_scripts()})
    target = tmp_path / "my kip dir"

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(target), "--no-bootstrap", "--keep-archive"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (target / "README.md").exists()
    assert (tmp_path / "kip-9.9.9.zip").exists() and (tmp_path / "kip-9.9.9.zip.sha256").exists()

    interrupted = tmp_path / "interrupted"
    interrupted.mkdir()
    (interrupted / "VERSION").write_text("9.9.9\n")
    (interrupted / "KIP-MANIFEST.json").write_text("{}")
    broken = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(interrupted), "--no-bootstrap"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert broken.returncode == 1 and "interrupted install" in broken.stderr


def test_rollback_accepts_a_partially_applied_upgrade(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    archive = _make_kit(tmp_path / "packages", "1.1.0", NEW_KIT)
    assert _run_upgrade(deployment, "--archive", str(archive)).returncode == 0
    # Simulate a commit that stopped before VERSION was replaced.
    (deployment / "VERSION").write_text("1.0.0\n")

    result = _run_upgrade(deployment, "--rollback")

    assert result.returncode == 0, result.stderr
    assert (deployment / "README.md").read_bytes() == b"old readme\n"
    (deployment / "VERSION").write_text("9.9.9\n")
    assert "matches neither" in _run_upgrade(deployment, "--rollback").stderr


def test_installer_keeps_a_user_created_empty_directory_on_failure(tmp_path: Path) -> None:
    files = {"README.md": b"# package\n", **_kit_scripts()}
    del files["scripts/bootstrap.sh"]  # layout check fails after verification
    _, env = _release_files(tmp_path, "9.9.9", files)
    target = tmp_path / "prepared"
    target.mkdir()

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(target), "--no-bootstrap"],
        env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 1 and "single package directory" in result.stderr
    assert target.is_dir() and not list(target.iterdir())


def test_installer_writes_a_global_launcher_and_an_idempotent_shell_profile_block(tmp_path: Path) -> None:
    _, env = _release_files(tmp_path, "9.9.9", {"README.md": b"# kit\n", **_kit_scripts()})
    home = Path(env["HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / ".zshrc").write_text("# existing rc\nexport FOO=1\n")
    env["SHELL"] = "/bin/zsh"
    target = tmp_path / "install"

    for _ in range(2):  # second run hits the already-installed path and must not duplicate the block
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/install.sh"), str(target), "--no-bootstrap"],
            env=env, capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr

    launcher = home / ".local/bin/kip"
    assert os.access(launcher, os.X_OK)
    assert str(target) in launcher.read_text()
    rc = (home / ".zshrc").read_text()
    assert rc.startswith("# existing rc\nexport FOO=1\n")
    assert rc.count("# >>> KIP >>>") == 1 and rc.count("# <<< KIP <<<") == 1
    assert f"export KIP_HOME='{target}'" in rc and ".local/bin" in rc
    assert "kip --help" in result.stderr or "kip --help" in result.stdout

    quiet = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(tmp_path / "second"), "--no-bootstrap", "--no-shell-profile", "--bin-dir", str(tmp_path / "bin")],
        env=env, capture_output=True, text=True, check=False,
    )
    assert quiet.returncode == 0, quiet.stderr
    assert (tmp_path / "bin/kip").exists()
    assert (home / ".zshrc").read_text() == rc


def test_upgrade_reads_legacy_manifest_name_and_installer_falls_back_to_legacy_asset(tmp_path: Path) -> None:
    deployment = _deploy(tmp_path, "1.0.0", OLD_KIT)
    legacy = deployment / "STARTER-KIT-MANIFEST.json"
    (deployment / "KIP-MANIFEST.json").rename(legacy)
    text = legacy.read_text().replace("kip.package-archive.v1", "kip.starter-archive.v1")
    legacy.write_text(text)
    releases = tmp_path / "releases" / "v1.1.0"
    archive = _make_kit(releases, "1.1.0", NEW_KIT)
    # Publish only under the pre-3.10.0 asset name, as older releases did.
    archive.rename(releases / "kip-starter-kit-1.1.0.zip")
    digest = hashlib.sha256((releases / "kip-starter-kit-1.1.0.zip").read_bytes()).hexdigest()
    (releases / "kip-starter-kit-1.1.0.zip.sha256").write_text(f"{digest}  kip-starter-kit-1.1.0.zip\n")
    (releases / "kip-1.1.0.zip.sha256").unlink()
    env = {**os.environ, "KIP_RELEASE_BASE_URL": (tmp_path / "releases").as_uri(), "KIP_VERSION": "1.1.0", "HOME": str(tmp_path / "home")}

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(deployment), "--no-bootstrap", "--no-shell-profile"],
        env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Verified kip-starter-kit-1.1.0.zip" in result.stderr
    assert (deployment / "VERSION").read_text().strip() == "1.1.0"
    assert (deployment / "KIP-MANIFEST.json").exists() and not legacy.exists()
    rollback = _run_upgrade(deployment, "--rollback")
    assert rollback.returncode == 0, rollback.stderr
    assert legacy.exists() and (deployment / "VERSION").read_text().strip() == "1.0.0"
    assert not (deployment / "KIP-MANIFEST.json").exists()


def test_launcher_and_profile_quote_hostile_paths_and_preserve_profile_identity(tmp_path: Path) -> None:
    _, env = _release_files(tmp_path, "9.9.9", {"README.md": b"# kit\n", **_kit_scripts()})
    home = Path(env["HOME"])
    (home / "dotfiles").mkdir(parents=True)
    real_rc = home / "dotfiles/zshrc"
    real_rc.write_text("export FOO=1\n")
    real_rc.chmod(0o600)
    (home / ".zshrc").symlink_to(real_rc)
    env["SHELL"] = "/bin/zsh"
    marker = tmp_path / "PWNED"
    target = tmp_path / f"kip 지식's $(touch {marker})dir"  # Korean, quote and command substitution
    bin_dir = tmp_path / 'bin "q'

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(target), "--no-bootstrap", "--bin-dir", str(bin_dir)],
        env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    launcher = bin_dir / "kip"
    # Running the launcher must resolve the literal directory, never execute it.
    subprocess.run(["/bin/bash", str(launcher)], env={**env, "PATH": os.environ["PATH"]}, capture_output=True, text=True, check=False)
    assert not marker.exists()
    assert (home / ".zshrc").is_symlink() and real_rc.read_text().startswith("export FOO=1\n")
    assert oct(real_rc.stat().st_mode & 0o777) == "0o600"
    for shell in ("/bin/zsh", "/bin/bash", "/bin/sh", *(["/bin/dash"] if Path("/bin/dash").exists() else [])):
        # The profile block must be plain POSIX quoting so every login shell can read it.
        parsed = subprocess.run([shell, "-c", f". {shlex.quote(str(real_rc))}; printf %s \"$KIP_HOME\""], capture_output=True, text=True, check=False)
        assert parsed.returncode == 0 and parsed.stdout == str(target), (shell, parsed.stderr)
    assert subprocess.run(["/bin/bash", "-n", str(launcher)], capture_output=True).returncode == 0

    dry = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(target), "--dry-run"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert dry.returncode == 0 and "already installed" in dry.stderr
    assert real_rc.read_text().count("# >>> KIP >>>") == 1


def test_installer_upgrades_with_the_upgrader_shipped_in_the_archive(tmp_path: Path) -> None:
    # A 3.9.x deployment carries an upgrader that only understands the former
    # manifest name; the installer must apply the archive with the archive's
    # own upgrader and then let the new tree finish (bootstrap, migrate, doctor).
    old_scripts = {
        **_kit_scripts(),
        "scripts/upgrade.sh": b"#!/bin/sh\necho OLD-UPGRADER-USED >&2\nexit 97\n",
        "scripts/upgrade_kit.py": b"# legacy upgrader\n",
    }
    del old_scripts["scripts/upgrade_package.py"]
    deployment = _deploy(tmp_path, "1.0.0", {"README.md": b"old\n", "config/kip.example.toml": b"[app]\n", **old_scripts})
    (deployment / "STARTER-KIT-MANIFEST.json").write_bytes((deployment / "KIP-MANIFEST.json").read_bytes())
    (deployment / "KIP-MANIFEST.json").unlink()
    new_kit = {
        "README.md": b"new\n", **_kit_scripts(),
        "scripts/migrate.sh": b"#!/bin/sh\necho migrate-stub\n",
        "scripts/kip": b"#!/bin/sh\nexit 0\n",
    }
    _, env = _release_files(tmp_path, "2.0.0", new_kit, executable={"scripts/migrate.sh", "scripts/kip", "scripts/bootstrap.sh", "scripts/upgrade.sh"})

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(deployment), "--no-shell-profile", "--bin-dir", str(tmp_path / "bin")],
        env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "OLD-UPGRADER-USED" not in result.stderr
    assert "Upgrade complete" in result.stdout
    assert (deployment / "VERSION").read_text().strip() == "2.0.0"
    assert (deployment / "README.md").read_bytes() == b"new\n"
    assert (deployment / "KIP-MANIFEST.json").exists() and not (deployment / "STARTER-KIT-MANIFEST.json").exists()
    assert not (deployment / "scripts/upgrade_kit.py").exists()


def test_legacy_archive_copy_matches_the_3_9_upgrader_contract(tmp_path: Path) -> None:
    archive = _make_kit(tmp_path / "packages", "2.0.0", {"README.md": b"# package\n", **_kit_scripts()})
    legacy = tmp_path / "kip-starter-kit-2.0.0.zip"
    for _ in range(2):  # deterministic
        run = subprocess.run([sys.executable, str(ROOT / "scripts/legacy_archive.py"), str(archive), str(legacy)], capture_output=True, text=True, check=False)
        assert run.returncode == 0, run.stderr
    first = legacy.read_bytes()
    subprocess.run([sys.executable, str(ROOT / "scripts/legacy_archive.py"), str(archive), str(legacy)], check=True, capture_output=True)
    assert legacy.read_bytes() == first

    with zipfile.ZipFile(legacy) as zipped, zipfile.ZipFile(archive) as original:
        root = zipped.namelist()[0].split("/")[0]
        names = {name.split("/", 1)[1]: zipped.read(name) for name in zipped.namelist()}
        assert "STARTER-KIT-MANIFEST.json" in names and "KIP-MANIFEST.json" not in names
        manifest = json.loads(names["STARTER-KIT-MANIFEST.json"])
        assert manifest["schema_version"] == "kip.starter-archive.v1" and manifest["root"] == root
        checksums = dict(line.split("  ", 1)[::-1] for line in names["SHA256SUMS"].decode().splitlines())
        payload = {name: data for name, data in names.items() if name != "SHA256SUMS"}
        # The exact predicates scripts/upgrade_kit.py (3.9.x) enforces before applying.
        assert set(checksums) == set(payload)
        assert set(manifest["files"]) == set(payload) - {"STARTER-KIT-MANIFEST.json"}
        for name, data in payload.items():
            assert checksums[name] == hashlib.sha256(data).hexdigest(), name
            if name != "STARTER-KIT-MANIFEST.json":
                assert manifest["files"][name] == f"sha256:{checksums[name]}"
        original_modes = {info.filename.split("/", 1)[1]: info.external_attr for info in original.infolist()}
        for info in zipped.infolist():
            relative = info.filename.split("/", 1)[1]
            if relative not in {"STARTER-KIT-MANIFEST.json", "SHA256SUMS"}:
                assert info.external_attr == original_modes[relative]
    # The current upgrader also applies the legacy-format copy and converges on the canonical manifest name.
    deployment = _deploy(tmp_path, "1.0.0", {"README.md": b"old\n", "config/kip.example.toml": b"[app]\n", **_kit_scripts()})
    applied = subprocess.run([sys.executable, str(ROOT / "scripts/upgrade_package.py"), "--deployment", str(deployment), "--archive", str(legacy)], capture_output=True, text=True, check=False)
    assert applied.returncode == 0, applied.stderr
    assert (deployment / "VERSION").read_text().strip() == "2.0.0"
    assert (deployment / "KIP-MANIFEST.json").is_file() and not (deployment / "STARTER-KIT-MANIFEST.json").exists()
    assert json.loads((deployment / "KIP-MANIFEST.json").read_text())["version"] == "2.0.0"


def test_installer_follows_a_relative_profile_symlink_under_zdotdir(tmp_path: Path) -> None:
    _, env = _release_files(tmp_path, "9.9.9", {"README.md": b"# kit\n", **_kit_scripts()})
    home = Path(env["HOME"])
    zdot = home / "zdot"
    (zdot / "dotfiles").mkdir(parents=True)
    real_rc = zdot / "dotfiles/zshrc"
    real_rc.write_text("# managed\n")
    os.symlink("dotfiles/zshrc", zdot / ".zshrc")  # relative link, as stow/chezmoi create
    (home / ".zshenv").write_text(f"ZDOTDIR={zdot}\n")  # set but not exported, the common form
    env["SHELL"] = "/bin/zsh"

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/install.sh"), str(tmp_path / "install"), "--no-bootstrap", "--bin-dir", str(tmp_path / "bin")],
        env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (zdot / ".zshrc").is_symlink()
    assert real_rc.read_text().startswith("# managed\n# >>> KIP >>>")
    assert not (home / "dotfiles").exists() and not (home / ".zshrc").exists()
