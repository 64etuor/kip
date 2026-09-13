from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "skills/knowledge-fabric/scripts/kip.sh"


def _runtime(root: Path, label: str) -> Path:
    (root / "scripts").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# KIP\n")
    executable = root / "scripts/kip"
    executable.write_text(f"#!/bin/bash\nprintf '%s\\n' '{label}' \"$@\"\n")
    executable.chmod(0o755)
    return root


def test_explicit_invalid_runtime_never_falls_back(tmp_path: Path) -> None:
    fallback = _runtime(tmp_path / "fallback", "wrong-workspace")
    result = subprocess.run(
        ["bash", str(BRIDGE), "capabilities"], cwd=fallback,
        env={**os.environ, "KIP_PROJECT_DIR": str(tmp_path / "missing")},
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert not result.stdout
    assert "KIP_PROJECT_DIR" in result.stderr


def test_explicit_runtime_preserves_arguments_with_spaces(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "KIP project", "selected-workspace")
    result = subprocess.run(
        ["bash", str(BRIDGE), "search", "협약 변경 승인"], cwd=tmp_path,
        env={**os.environ, "KIP_PROJECT_DIR": str(runtime)},
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.splitlines() == ["selected-workspace", "search", "협약 변경 승인"]


def test_verify_missing_tools_fails_before_running_checks(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts/verify.sh", scripts / "verify.sh")
    (scripts / "common.sh").write_text(
        'PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"\n'
        'python_cmd() { printf "%s\\n" "$PROJECT_ROOT/fake-python"; }\n'
    )
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        '#!/bin/bash\ncase "$*" in\n'
        '  *--version*) exit 1 ;;\n'
        '  *) printf "%s\\n" "$*" >> "${0%/*}/checks-ran" ;;\nesac\n'
    )
    fake_python.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(scripts / "verify.sh")],
        env={**os.environ, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "bootstrap.sh" in result.stderr
    assert "Verification passed" not in result.stdout
    assert not (tmp_path / "checks-ran").exists()


@pytest.mark.parametrize("supplied_test_db", [False, True])
@pytest.mark.parametrize("use_uv", [True, False])
def test_verify_runs_every_gate_with_the_selected_environment(
    tmp_path: Path, use_uv: bool, supplied_test_db: bool
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / "examples").mkdir()
    shutil.copy2(ROOT / "scripts/verify.sh", scripts / "verify.sh")
    # The npm gate is a separate executable; verify.sh must invoke it before
    # the Python checks, even when Node/npm are only available to that script.
    (scripts / "audit-kordoc.sh").write_text(
        '#!/bin/bash\nprintf "audit-kordoc\\n" >> "${0%/*}/../commands"\n'
    )
    (scripts / "audit-kordoc.sh").chmod(0o755)
    (scripts / "audit-semantic.sh").write_text(
        '#!/bin/bash\nprintf "audit-semantic\\n" >> "${0%/*}/../commands"\n'
    )
    (scripts / "audit-semantic.sh").chmod(0o755)
    shutil.copy2(ROOT / "scripts/golden-gate.sh", scripts / "golden-gate.sh")
    (scripts / "common.sh").write_text(
        'PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"\n'
        'python_cmd() { printf "%s\\n" "$PROJECT_ROOT/fake-python"; }\n'
    )
    # verify.sh starts a throwaway PostgreSQL through e2e-common.sh when no test
    # database is named; the stub records that instead of starting Docker.
    (scripts / "e2e-common.sh").write_text(
        'e2e_start_postgres() { printf "e2e_start_postgres\\n" >> "$E2E_PROJECT_ROOT/commands"; '
        'E2E_DATABASE_URL="postgresql://throwaway/kip"; }\n'
        'e2e_stop_postgres() { printf "e2e_stop_postgres\\n" >> "$E2E_PROJECT_ROOT/commands"; }\n'
    )
    for name in ("fake-python", "uv") if use_uv else ("fake-python",):
        stub = tmp_path / name
        stub.write_text(
            '#!/bin/bash\nprintf "%s\\n" "$*" >> "${0%/*}/commands"\n'
            'case "$*" in *"pytest"*) printf "test-db=%s\\n" "${KIP_TEST_POSTGRES_URL:-}" '
            '>> "${0%/*}/commands" ;; esac\n'
        )
        stub.chmod(0o755)
    # Pin both variables the database block reads: CI sets GITHUB_ACTIONS, which
    # would skip the block and let this test pass there while failing locally.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"KIP_TEST_POSTGRES_URL", "GITHUB_ACTIONS"}
    }
    environment["PATH"] = f"{tmp_path}:/usr/bin:/bin"
    if supplied_test_db:
        environment["KIP_TEST_POSTGRES_URL"] = "postgresql://supplied/kip"
    result = subprocess.run(
        ["/bin/bash", str(scripts / "verify.sh")],
        env=environment,
        capture_output=True, text=True, check=True,
    )
    commands = (tmp_path / "commands").read_text().splitlines()
    if supplied_test_db:
        assert "e2e_start_postgres" not in commands
        assert "test-db=postgresql://supplied/kip" in commands
    else:
        assert commands.index("e2e_start_postgres") < commands.index(
            "test-db=postgresql://throwaway/kip"
        )
        assert "e2e_stop_postgres" in commands
    prefix = "run --frozen python -m" if use_uv else "-m"
    assert commands.index("audit-kordoc") < commands.index(f"{prefix} ruff check src tests scripts")
    assert f"{prefix} ruff check src tests scripts" in commands
    assert f"{prefix} mypy src/kip" in commands
    assert f"{prefix} pip_audit --requirement requirements/runtime.txt --no-deps --disable-pip" in commands
    assert ("run --frozen pytest" if use_uv else "-m pytest") in commands
    assert "audit-semantic" in commands
    assert any(line.endswith("scripts/portable_golden_gate.py") for line in commands)
    assert any(line.endswith("scripts/golden_gate.py") for line in commands)
    assert "Verification passed" in result.stdout


def test_sourcing_common_twice_does_not_duplicate_path_entries() -> None:
    result = subprocess.run(
        ["/bin/bash", "-c", 'source scripts/common.sh; source scripts/common.sh; source scripts/runtime-path.sh; printf "%s" "$PATH"'],
        cwd=ROOT, capture_output=True, text=True, check=True,
        env={**os.environ, "KIP_SKIP_DOTENV": "1"},
    )
    entries = result.stdout.split(":")
    assert entries.count(str(ROOT / "scripts")) == 1
