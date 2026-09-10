from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _audit_project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for name in ("common.sh", "audit-kordoc.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    shutil.copytree(ROOT / "requirements/kordoc", project / "requirements/kordoc")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$KIP_NPM_LOG"\n'
        'exit "${KIP_NPM_EXIT:-0}"\n',
        encoding="utf-8",
    )
    npm.chmod(0o755)
    return project, {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "KIP_SKIP_DOTENV": "1",
        "KIP_NPM_LOG": str(tmp_path / "npm.log"),
    }


def _run(project: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(project / "scripts/audit-kordoc.sh")],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_audit_reads_locked_graph_without_installing_or_changing_it(tmp_path: Path) -> None:
    project, env = _audit_project(tmp_path)
    manifest = project / "requirements/kordoc/package.json"
    lock = project / "requirements/kordoc/package-lock.json"
    before = manifest.read_bytes(), lock.read_bytes()

    result = _run(project, env)

    assert result.returncode == 0, result.stderr
    assert Path(env["KIP_NPM_LOG"]).read_text().splitlines() == [
        "audit --package-lock-only --omit=dev --audit-level=high"
    ]
    assert (manifest.read_bytes(), lock.read_bytes()) == before
    assert not (manifest.parent / "node_modules").exists()


@pytest.mark.parametrize("exit_code", [1, 42])
def test_audit_propagates_high_findings_and_registry_errors(
    tmp_path: Path, exit_code: int,
) -> None:
    project, env = _audit_project(tmp_path)

    result = _run(project, {**env, "KIP_NPM_EXIT": str(exit_code)})

    assert result.returncode == exit_code


@pytest.mark.parametrize("drift", ["root", "package", "override", "missing"])
def test_audit_rejects_manifest_lock_drift_before_network(
    tmp_path: Path, drift: str,
) -> None:
    project, env = _audit_project(tmp_path)
    lock_path = project / "requirements/kordoc/package-lock.json"
    lock = json.loads(lock_path.read_text())
    if drift == "root":
        lock["packages"][""]["dependencies"]["kordoc"] = "4.7.3"
    elif drift == "package":
        lock["packages"]["node_modules/kordoc"]["version"] = "4.7.3"
    elif drift == "override":
        lock["packages"]["node_modules/sharp"]["version"] = "0.35.3"
    else:
        del lock["packages"]["node_modules/adm-zip"]
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    result = _run(project, env)

    assert result.returncode != 0
    assert "lock" in result.stderr.lower()
    assert not Path(env["KIP_NPM_LOG"]).exists()


@pytest.mark.parametrize("missing", ["node", "npm"])
def test_audit_fails_when_required_tool_is_missing(tmp_path: Path, missing: str) -> None:
    project, env = _audit_project(tmp_path)
    isolated_bin = tmp_path / "only-required-tools"
    isolated_bin.mkdir()
    for name in ("bash", "dirname", "node", "npm"):
        if name == missing:
            continue
        executable = shutil.which(name)
        assert executable is not None
        (isolated_bin / name).symlink_to(executable)

    result = _run(project, {**env, "PATH": str(isolated_bin)})

    assert result.returncode != 0
    assert "npm" in result.stderr


def test_installer_audit_failure_preserves_existing_runtime(tmp_path: Path) -> None:
    project, env = _audit_project(tmp_path)
    for name in ("install-kordoc.sh", "kordoc", "kordoc-runtime.sh"):
        shutil.copy2(ROOT / "scripts" / name, project / "scripts" / name)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sentinel = runtime / "package.json"
    sentinel.write_text('{"preserve": true}\n', encoding="utf-8")

    result = subprocess.run(
        [str(project / "scripts/install-kordoc.sh")],
        cwd=project,
        env={**env, "KIP_NPM_EXIT": "1", "KIP_KORDOC_INSTALL_ROOT": str(runtime)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert sentinel.read_text() == '{"preserve": true}\n'
    assert not (runtime / "package-lock.json").exists()
    assert "ci " not in Path(env["KIP_NPM_LOG"]).read_text()


def test_launcher_default_root_follows_the_manifest(tmp_path: Path) -> None:
    project, env = _audit_project(tmp_path)
    for name in ("kordoc", "kordoc-runtime.sh"):
        shutil.copy2(ROOT / "scripts" / name, project / "scripts" / name)
    manifest = json.loads((project / "requirements/kordoc/package.json").read_text())
    version = manifest["dependencies"]["kordoc"]
    revision = manifest["version"].split(".")[0]
    cli = project / f"var/kordoc-{version}-r{revision}/node_modules/kordoc/dist/cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text('console.log(process.argv.slice(2).join(" "))\n', encoding="utf-8")

    result = subprocess.run(
        [str(project / "scripts/kordoc"), "--version"],
        cwd=project, env=env, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "--version"
    # A bumped manifest moves the expected root, so the stale runtime is
    # reported as missing instead of silently reused.
    manifest["version"] = "3.0.0"
    (project / "requirements/kordoc/package.json").write_text(json.dumps(manifest), encoding="utf-8")
    moved = subprocess.run(
        [str(project / "scripts/kordoc"), "--version"],
        cwd=project, env=env, capture_output=True, text=True, check=False,
    )
    assert moved.returncode == 69
    assert f"Kordoc {version} is not installed" in moved.stderr


def test_audit_fails_closed_when_manifest_has_no_overrides(tmp_path: Path) -> None:
    project, env = _audit_project(tmp_path)
    manifest_path = project / "requirements/kordoc/package.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["overrides"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run(project, env)

    assert result.returncode != 0
    assert "Kordoc lock validation failed" in result.stderr
    assert not Path(env["KIP_NPM_LOG"]).exists()
