from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kip.setup.models import SecretReference
from kip.setup.planner import build_setup_plan
from kip.setup.writer import apply_setup_plan
from tests.setup_support import complete_setup_answers

ROOT = Path(__file__).resolve().parents[1]


def _installation(tmp_path: Path, *, external: bool = False, generated: bool = True):
    answers = complete_setup_answers(tmp_path).model_copy(update={
        "identity_mode": "api_key",
        "identity_api_key_secret_ref": SecretReference.parse("env:AUDIT_API_KEY"),
        "identity_admin_key_secret_ref": SecretReference.parse("env:AUDIT_ADMIN_KEY"),
        "model_secret_ref": SecretReference.parse("env:AUDIT_MODEL_KEY"),
        "database_secret_ref": SecretReference.parse("env:AUDIT_DATABASE_URL" if external else "env:KIP_DATABASE_URL"),
    })
    project = tmp_path / "project"
    if generated:
        apply_setup_plan(build_setup_plan(answers, project_root=project), project_root=project)
    scripts = project / "scripts"
    scripts.mkdir()
    for name in ("app-up.sh", "common.sh", "load_dotenv.py", "setup_compose.py"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    # The kip_api / kip_worker / kip_backup login passwords live in .env beside
    # POSTGRES_PASSWORD: compose interpolates them for the api, worker and
    # `roles` services of a bundled-database project.
    (project / ".env").write_text(
        f"AUDIT_API_KEY_FILE={tmp_path / 'missing-api-secret'}\n"
        "KIP_API_DB_PASSWORD=test-api-password\n"
        "KIP_WORKER_DB_PASSWORD=test-worker-password\n"
        "KIP_BACKUP_DB_PASSWORD=test-backup-password\n"
    )
    binary = project / ".venv/bin"
    binary.mkdir(parents=True)
    (binary / "python").write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    (binary / "python").chmod(0o755)
    trace = tmp_path / "calls.jsonl"
    fake = (
        f"#!{sys.executable}\nimport json,os,sys\n"
        "with open(os.environ['AUDIT_TRACE'],'a') as f:\n"
        " f.write(json.dumps({'command':os.path.basename(sys.argv[0]),'args':sys.argv[1:],"
        "'config':os.environ.get('KIP_CONFIG'),'db':os.environ.get('AUDIT_DATABASE_URL'),"
        "'api':os.environ.get('AUDIT_API_KEY'),'api_file':os.environ.get('AUDIT_API_KEY_FILE')})+'\\n')\n"
        "raise SystemExit(int(os.environ.get('AUDIT_DOCKER_EXIT' if os.path.basename(sys.argv[0])=='docker' else 'AUDIT_MIGRATE_EXIT','0')))\n"
    )
    for file in (binary / "docker", scripts / "migrate.sh"):
        file.write_text(fake)
        file.chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("KIP_", "POSTGRES_", "AUDIT_"))}
    environment.update({
        "PATH": f"{binary}:{os.environ['PATH']}", "PYTHONPATH": str(ROOT / "src"),
        "AUDIT_TRACE": str(trace), "POSTGRES_PASSWORD": "test-password",
        "KIP_DATABASE_URL": "postgresql://kip_owner:test-password@127.0.0.1:5432/kip",
        "AUDIT_DATABASE_URL": "postgresql://synthetic.invalid/selected",
        "AUDIT_API_KEY_FILE": str(tmp_path / "missing-api-secret"),
        "AUDIT_ADMIN_KEY_FILE": str(tmp_path / "missing-admin-secret"),
        "AUDIT_MODEL_KEY_FILE": str(tmp_path / "missing-model-secret"),
    })
    return project, environment, trace


def _run(project, environment, *args):
    return subprocess.run([str(project / "scripts/app-up.sh"), *args], cwd=project, env=environment, capture_output=True, text=True)


def _calls(trace):
    return [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []


@pytest.mark.parametrize("generated", [True, False])
def test_database_only_waits_then_migrates_without_building(tmp_path: Path, generated: bool) -> None:
    project, environment, trace = _installation(tmp_path, generated=generated)
    result = _run(project, environment, "--database-only")
    assert result.returncode == 0, result.stderr
    calls = _calls(trace)
    # postgres, host migrations, then the one-shot `roles` service: the kip_api,
    # kip_worker and kip_backup logins belong to the database, not to the app
    # profile, and their grants cover the tables migrations just created.
    assert [call["command"] for call in calls] == ["docker", "migrate.sh", "docker"]
    assert calls[0]["args"][-6:] == ["up", "-d", "--wait", "--wait-timeout", "60", "postgres"]
    assert "--build" not in calls[0]["args"]
    assert calls[2]["args"][-4:] == ["run", "--rm", "--no-deps", "roles"]
    assert "--build" not in calls[2]["args"]
    assert "Database ready and migrations complete" in result.stdout
    if generated:
        assert calls[1]["config"] == str(project / "config/kip.host.generated.toml")
        assert calls[1]["api"] and calls[1]["api_file"] is None
        assert "compose.generated.yaml" in calls[2]["args"]


def test_database_only_external_never_invokes_compose(tmp_path: Path) -> None:
    project, environment, trace = _installation(tmp_path, external=True)
    result = _run(project, environment, "--database-only")
    assert result.returncode == 0, result.stderr
    calls = _calls(trace)
    assert [call["command"] for call in calls] == ["migrate.sh"]
    assert calls[0]["db"] == environment["AUDIT_DATABASE_URL"]


def test_database_only_external_resolves_database_secret_file_for_migration(tmp_path: Path) -> None:
    project, environment, trace = _installation(tmp_path, external=True)
    secret = tmp_path / "database-secret"
    selected = environment.pop("AUDIT_DATABASE_URL")
    secret.write_text(selected + "\n")
    environment["AUDIT_DATABASE_URL_FILE"] = str(secret)
    result = _run(project, environment, "--database-only")
    assert result.returncode == 0, result.stderr
    assert _calls(trace)[0]["db"] == selected


def test_database_only_compose_interpolation_does_not_need_model_or_identity_secrets(tmp_path: Path) -> None:
    real_docker = shutil.which("docker")
    if real_docker is None:
        pytest.skip("Docker Compose CLI unavailable")
    project, environment, trace = _installation(tmp_path)
    # Two invocations now: postgres, then the one-shot `roles` service. Both
    # must interpolate from .env alone.
    (project / ".venv/bin/docker").write_text(
        f"#!{sys.executable}\nimport subprocess,sys\n"
        "argv=sys.argv[1:]\n"
        "starts_postgres=argv[-6:]==['up','-d','--wait','--wait-timeout','60','postgres']\n"
        "assert starts_postgres or argv[-4:]==['run','--rm','--no-deps','roles']\n"
        "head=argv[:-6] if starts_postgres else argv[:-4]\n"
        f"raise SystemExit(subprocess.run([{real_docker!r},*head,'config','--quiet']).returncode)\n"
    )
    result = _run(project, environment, "--database-only")
    assert result.returncode == 0, result.stderr
    assert [call["command"] for call in _calls(trace)] == ["migrate.sh"]


def test_database_only_migration_does_not_reload_unused_secret_files(tmp_path: Path) -> None:
    project, environment, trace = _installation(tmp_path, external=True)
    (project / "scripts/migrate.sh").write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\n'
        'source "$(dirname "$0")/common.sh"\n'
        '"$(python_cmd)" -c \'from kip.settings import Settings; Settings.load()\'\n'
    )
    result = _run(project, environment, "--database-only")
    assert result.returncode == 0, result.stderr
    assert not _calls(trace)


def test_database_only_refuses_mismatched_host_config_before_starting(tmp_path: Path) -> None:
    project, environment, trace = _installation(tmp_path)
    host = project / "config/kip.host.generated.toml"
    host.write_text(host.read_text().replace('plan_fingerprint = "', 'plan_fingerprint = "mismatched-'))
    result = _run(project, environment, "--database-only")
    assert result.returncode != 0
    assert not _calls(trace)


@pytest.mark.parametrize("external", [False, True])
def test_database_only_missing_database_secret_fails_closed(tmp_path: Path, external: bool) -> None:
    project, environment, trace = _installation(tmp_path, external=external)
    environment.pop("AUDIT_DATABASE_URL" if external else "KIP_DATABASE_URL")
    result = _run(project, environment, "--database-only")
    assert result.returncode != 0
    assert not _calls(trace)
    assert "Database ready" not in result.stdout


@pytest.mark.parametrize("failure", ["AUDIT_DOCKER_EXIT", "AUDIT_MIGRATE_EXIT"])
def test_database_only_failure_never_claims_ready(tmp_path: Path, failure: str) -> None:
    project, environment, trace = _installation(tmp_path)
    environment[failure] = "17"
    result = _run(project, environment, "--database-only")
    assert result.returncode == 17
    assert "Database ready" not in result.stdout
    if failure == "AUDIT_DOCKER_EXIT":
        assert [call["command"] for call in _calls(trace)] == ["docker"]


@pytest.mark.parametrize("args", [("--database-only", "extra"), ("--down", "extra"), ("--unknown",)])
def test_app_up_rejects_unknown_and_extra_arguments(tmp_path: Path, args: tuple[str, ...]) -> None:
    project, environment, trace = _installation(tmp_path)
    result = _run(project, environment, *args)
    assert result.returncode == 2
    assert not _calls(trace)


def test_default_full_app_preserves_build_and_does_not_host_migrate(tmp_path: Path) -> None:
    project, environment, trace = _installation(tmp_path)
    (project / ".env").unlink()
    for name in ("AUDIT_API_KEY", "AUDIT_ADMIN_KEY", "AUDIT_MODEL_KEY"):
        environment.pop(f"{name}_FILE")
        environment[name] = "synthetic-key"
    result = _run(project, environment)
    assert result.returncode == 0, result.stderr
    calls = _calls(trace)
    assert [call["command"] for call in calls] == ["docker"]
    assert calls[0]["args"][-3:] == ["up", "-d", "--build"]
