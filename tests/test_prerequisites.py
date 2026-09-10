from __future__ import annotations

import hashlib
import importlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def prereq(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("prerequisites")


class Response(io.BytesIO):
    url = "https://example.test/runtime.tar.gz"


def archive_bytes(name="uv-test/uv", body=b"#!/bin/sh\nprintf 'uv 0.12.12\\n'\n"):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(body)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(body))
    return stream.getvalue()


def test_checksum_failure_never_executes_download(prereq, tmp_path, monkeypatch):
    data = archive_bytes()
    monkeypatch.setattr(prereq.urllib.request, "urlopen", lambda *args, **kwargs: Response(data))
    monkeypatch.setattr(prereq, "run", lambda *args, **kwargs: pytest.fail("unverified binary executed"))
    asset = prereq.Asset("uv", "linux-x64", "0.12.12", Response.url, "0" * 64)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        prereq.prepare_bundle(tmp_path, asset)
    assert not (tmp_path / "var/runtime/uv-0.12.12").exists()
    assert list((tmp_path / "var/runtime").glob(".download-*")) == []


def test_verified_runtime_is_reused_without_download(prereq, tmp_path, monkeypatch):
    data = archive_bytes()
    asset = prereq.Asset("uv", "linux-x64", "0.12.12", Response.url, hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(prereq.urllib.request, "urlopen", lambda *args, **kwargs: Response(data))
    path = prereq.prepare_bundle(tmp_path, asset)
    monkeypatch.setattr(prereq.urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("download repeated"))
    assert prereq.prepare_bundle(tmp_path, asset) == path
    assert (path / ".verified-sha256").read_text().strip() == asset.sha256


def test_unsafe_archive_and_existing_files_are_preserved(prereq, tmp_path, monkeypatch):
    data = archive_bytes("../../escape")
    asset = prereq.Asset("uv", "linux-x64", "0.12.12", Response.url, hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(prereq.urllib.request, "urlopen", lambda *args, **kwargs: Response(data))
    with pytest.raises(tarfile.TarError):
        prereq.prepare_bundle(tmp_path, asset)
    assert not (tmp_path / "escape").exists()
    existing = tmp_path / "var/runtime/uv-0.12.12"
    existing.mkdir()
    (existing / "owned.txt").write_text("keep")
    with pytest.raises(prereq.ActionRequired, match="Incomplete"):
        prereq.prepare_bundle(tmp_path, asset)
    assert (existing / "owned.txt").read_text() == "keep"


def test_runtime_paths_cannot_escape_project(prereq, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "var").symlink_to(outside, target_is_directory=True)
    asset = prereq.Asset("uv", "linux-x64", "0.12.12", Response.url, "0" * 64)
    with pytest.raises(prereq.ActionRequired):
        prereq.prepare_bundle(project, asset)
    with pytest.raises(prereq.ActionRequired):
        prereq.link_program(project, "node", Path("/usr/bin/node"))
    assert list(outside.iterdir()) == []


def test_check_only_does_not_install_or_write(prereq, tmp_path, monkeypatch):
    (tmp_path / "requirements").mkdir()
    shutil.copy2(ROOT / "requirements/bootstrap.tsv", tmp_path / "requirements/bootstrap.tsv")
    monkeypatch.setattr(prereq, "ROOT", tmp_path)
    monkeypatch.setattr(prereq, "target_platform", lambda: "linux-x64")
    monkeypatch.setattr(prereq, "prepare_bundle", lambda *args: pytest.fail("check installed software"))
    monkeypatch.setattr(prereq, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "v22.23.2\n" if args[0] == "node" else "10.0.0\n", ""))
    monkeypatch.setattr(sys, "argv", ["prerequisites", "--check"])
    assert prereq.main() == 0
    assert not (tmp_path / "var").exists()


def test_remote_docker_context_is_not_replaced(prereq, tmp_path, monkeypatch):
    monkeypatch.setattr(prereq, "docker_ready", lambda: False)
    monkeypatch.setattr(prereq.shutil, "which", lambda name: "/bin/docker")
    monkeypatch.setattr(prereq, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "2.39.0\n" if args[1:3] == ["compose", "version"] else "remote-production\n", ""))
    monkeypatch.setattr(prereq.subprocess, "run", lambda *args, **kwargs: pytest.fail("changed remote engine"))
    with pytest.raises(prereq.ActionRequired, match="will not replace"):
        prereq.ensure_docker(tmp_path, {}, "darwin-arm64", install=True, check=False)


def test_desktop_wait_does_not_accept_license_or_claim_success(prereq, tmp_path, monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr(prereq, "docker_ready", lambda: False)
    monkeypatch.setattr(prereq.shutil, "which", lambda name: "/bin/docker")
    monkeypatch.setattr(prereq, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "2.39.0\n" if args[1:3] == ["compose", "version"] else "default\n", ""))
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda path: True if str(path) == "/Applications/Docker.app" else real_exists(path))
    times = iter([0, 61])
    monkeypatch.setattr(prereq.time, "monotonic", lambda: next(times))
    calls = []
    monkeypatch.setattr(prereq.subprocess, "run", lambda args, **kwargs: calls.append(args))
    with pytest.raises(prereq.ActionRequired, match="not accessible"):
        prereq.ensure_docker(tmp_path, {}, "darwin-arm64", install=False, check=False)
    assert calls == [["open", "-a", "Docker"]]


def test_system_installation_needs_explicit_authorization(prereq, tmp_path, monkeypatch):
    monkeypatch.setattr(prereq, "docker_ready", lambda: False)
    monkeypatch.setattr(prereq.shutil, "which", lambda name: None)
    monkeypatch.setattr(prereq.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(prereq, "install_docker", lambda *args, **kwargs: pytest.fail("silent system install"))
    with pytest.raises(prereq.ActionRequired, match="--install-docker"):
        prereq.ensure_docker(tmp_path, {}, "linux-x64", install=False, check=False)


def test_linux_compose_install_does_not_upgrade_or_restart_engine(prereq, tmp_path, monkeypatch):
    monkeypatch.setattr(prereq.platform, "release", lambda: "6.8.0")
    monkeypatch.setattr(prereq.platform, "freedesktop_os_release", lambda: {"ID": "ubuntu", "VERSION_CODENAME": "noble"})
    monkeypatch.setattr(prereq, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", ""))
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda path: True if str(path).endswith("docker.sources") else real_exists(path))
    calls = []
    monkeypatch.setattr(prereq, "privileged", calls.append)
    prereq.install_docker(tmp_path, {}, "linux-x64", compose_only=True)
    assert calls == [["apt-get", "update"], ["apt-get", "install", "-y", "docker-compose-plugin"]]


@pytest.mark.parametrize("switches", [["--check"], ["--install-docker", "--without-docker"], ["--invalid"]])
def test_shell_handles_no_python_without_loading_dotenv(tmp_path, switches):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    for name in ("prerequisites.sh", "runtime-path.sh"):
        shutil.copy2(ROOT / "scripts" / name, project / "scripts" / name)
    (project / ".env").write_text("TRAP=$(touch should-not-exist)\n")
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    for name in ("bash", "dirname"):
        (bin_path / name).symlink_to(shutil.which(name))
    environment = {key: value for key, value in os.environ.items() if not key.startswith("KIP_")}
    environment["PATH"] = str(bin_path)
    result = subprocess.run([str(bin_path / "bash"), str(project / "scripts/prerequisites.sh"), *switches], env=environment, cwd=project, capture_output=True, text=True)
    assert result.returncode == (1 if switches == ["--check"] else 2)
    if switches == ["--check"]:
        assert "Python 3.12+: missing" in result.stderr
    assert not (project / "var").exists()
    assert not (project / "should-not-exist").exists()


def test_bootstrap_stops_before_common_when_prerequisites_need_action(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts/bootstrap.sh", scripts / "bootstrap.sh")
    (scripts / "prerequisites.sh").write_text("#!/bin/bash\nexit 75\n")
    (scripts / "prerequisites.sh").chmod(0o755)
    (scripts / "common.sh").write_text("touch unexpected\n")
    result = subprocess.run(["bash", str(scripts / "bootstrap.sh")], cwd=tmp_path)
    assert result.returncode == 75
    assert not (tmp_path / "unexpected").exists()


def test_bootstrap_and_ci_use_the_same_uv_pin(prereq):
    catalog = prereq.assets(ROOT)
    versions = {asset.version for asset in catalog.values() if asset.component == "uv"}
    assert len(versions) == 1
    assert f'UV_VERSION: "{versions.pop()}"' in (ROOT / ".github/workflows/ci.yml").read_text()


def test_duplicate_pins_cannot_make_shell_and_python_select_different_assets(prereq, tmp_path):
    (tmp_path / "requirements").mkdir()
    row = "uv linux-x64 0.12.12 https://example.test/uv " + "0" * 64
    (tmp_path / "requirements/bootstrap.tsv").write_text(row + "\n" + row + "\n")
    with pytest.raises(ValueError, match="duplicate"):
        prereq.assets(tmp_path)


def test_docker_authentication_is_checked_before_large_download(prereq, tmp_path, monkeypatch):
    monkeypatch.setattr(prereq.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(prereq.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(prereq.shutil, "which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr(prereq, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", ""))
    monkeypatch.setattr(prereq.platform, "mac_ver", lambda: ("14.0", (), ""))
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda path: False if str(path) == "/Applications/Docker.app" else real_exists(path))
    monkeypatch.setattr(prereq, "download", lambda *args: pytest.fail("download before required authentication"))
    with pytest.raises(prereq.ActionRequired, match="interactive terminal"):
        prereq.install_docker(tmp_path, {}, "darwin-arm64")


def test_download_size_is_bounded_before_checksum(prereq, tmp_path, monkeypatch):
    monkeypatch.setattr(prereq.urllib.request, "urlopen", lambda *args, **kwargs: Response(b"x" * (1024 * 1024 + 1)))
    asset = prereq.Asset("docker-key", "ubuntu", "current", Response.url, "0" * 64)
    with pytest.raises(RuntimeError, match="size limit"):
        prereq.download(asset, tmp_path / "key")


def test_container_path_does_not_prefer_writable_managed_binaries():
    result = subprocess.run(
        ["bash", "-c", 'source scripts/runtime-path.sh; printf "%s" "$PATH"'],
        cwd=ROOT, env={**os.environ, "PATH": "/usr/bin:/bin", "KIP_USE_MANAGED_RUNTIMES": "0"},
        capture_output=True, text=True, check=True,
    )
    assert "/var/runtime/bin" not in result.stdout
    assert "KIP_USE_MANAGED_RUNTIMES=0" in (ROOT / "Dockerfile").read_text()


def test_runtime_version_probe_does_not_accept_a_prefix_match(prereq):
    asset = prereq.Asset("uv", "linux-x64", "0.12.12", Response.url, "0" * 64)
    assert prereq.asset_version_ok(asset, "uv 0.12.12 (revision)")
    assert not prereq.asset_version_ok(asset, "uv 0.12.123")


def test_make_targets_find_project_managed_uv(tmp_path):
    shutil.copy2(ROOT / "Makefile", tmp_path / "Makefile")
    (tmp_path / "scripts").mkdir()
    for name in ("uv.sh", "common.sh", "runtime-path.sh"):
        shutil.copy2(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    binary = tmp_path / "var/runtime/bin/uv"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nprintf "%s" "$*" > "$KIP_TEST_UV_CALL"\n')
    binary.chmod(0o755)
    record = tmp_path / "called"
    result = subprocess.run(
        [shutil.which("make"), "lint"], cwd=tmp_path,
        env={**os.environ, "PATH": "/usr/bin:/bin", "KIP_TEST_UV_CALL": str(record)},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert record.read_text() == "run ruff check src tests scripts"


def test_desktop_installer_uses_hardware_architecture_without_accepting_license(prereq, tmp_path, monkeypatch):
    monkeypatch.setattr(prereq.platform, "mac_ver", lambda: ("14.0", (), ""))
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda path: False if str(path) == "/Applications/Docker.app" else real_exists(path))
    monkeypatch.setattr(prereq, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "1\n", ""))
    commands = []
    monkeypatch.setattr(prereq, "privileged", commands.append)
    monkeypatch.setattr(prereq.subprocess, "run", lambda args, **kwargs: commands.append(args))
    downloaded = []
    monkeypatch.setattr(prereq, "download", lambda asset, path: downloaded.append(asset.target))
    asset = prereq.Asset("docker", "darwin-arm64", "4.90.0", "https://example.test/Docker.dmg", "0" * 64)
    prereq.install_docker(tmp_path, {("docker", "darwin-arm64"): asset}, "darwin-x64")
    assert downloaded == ["darwin-arm64"]
    assert not any("--accept-license" in arg for command in commands for arg in command)
    assert any(command[0] == "hdiutil" and command[1] == "detach" for command in commands)
