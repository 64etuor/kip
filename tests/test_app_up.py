from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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
    for name in (
        "app-up.sh", "common.sh", "load_dotenv.py", "setup_compose.py", "dev-up.sh", "dev-down.sh",
        "postgres-tools.sh", "backup.sh", "ops-report.sh", "secret_value.py", "doctor.sh", "kordoc-runtime.sh",
    ):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    if not (project / "compose.yaml").exists():
        shutil.copy2(ROOT / "compose.yaml", project / "compose.yaml")
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
        # `docker ps` is the Compose project guard's query: it lists the
        # working directories of the project's existing containers.
        "if os.path.basename(sys.argv[0])=='docker' and sys.argv[1:2]==['ps']:\n"
        " print(os.environ.get('AUDIT_COMPOSE_WORKDIRS',''))\n"
        " raise SystemExit(int(os.environ.get('AUDIT_DOCKER_PS_EXIT','0')))\n"
        "raise SystemExit(int(os.environ.get('AUDIT_DOCKER_EXIT' if os.path.basename(sys.argv[0])=='docker' else 'AUDIT_MIGRATE_EXIT','0')))\n"
    )
    for file in (binary / "docker", scripts / "migrate.sh"):
        file.write_text(fake)
        file.chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("KIP_", "POSTGRES_", "AUDIT_", "COMPOSE_"))}
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


def _records(trace):
    return [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []


def _calls(trace):
    """Compose and migration calls, without the project guard's `docker ps` queries."""
    return [call for call in _records(trace) if not (call["command"] == "docker" and call["args"][:1] == ["ps"])]


def _guard_queries(trace):
    return [call["args"] for call in _records(trace) if call["command"] == "docker" and call["args"][:1] == ["ps"]]


def _queried_project(trace):
    [query] = _guard_queries(trace)
    return next(arg for arg in query if arg.startswith("label=com.docker.compose.project="))


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


@pytest.mark.parametrize("generated", [True, False])
@pytest.mark.parametrize("args", [("--database-only",), ("--down",), ()])
def test_refuses_a_compose_project_created_by_another_deployment(tmp_path: Path, generated: bool, args: tuple[str, ...]) -> None:
    project, environment, trace = _installation(tmp_path, generated=generated)
    other = tmp_path / "other-deployment"
    other.mkdir()
    environment["AUDIT_COMPOSE_WORKDIRS"] = f"{project}\n{other}"
    result = _run(project, environment, *args)
    assert result.returncode == 2
    assert f"belongs to another deployment at {other}" in result.stderr
    assert "COMPOSE_PROJECT_NAME=<unique-name>" in result.stderr
    assert "KIP_COMPOSE_ADOPT=1" in result.stderr
    assert "docker compose -p kip down" in result.stderr
    # Refused before any Compose call or migration touched the shared project.
    assert not _calls(trace)
    assert _queried_project(trace) == "label=com.docker.compose.project=kip"
    assert "Database ready" not in result.stdout


@pytest.mark.parametrize("generated", [True, False])
@pytest.mark.parametrize("owners", ["this deployment", "no containers", "docker unreachable"])
def test_proceeds_when_the_compose_project_is_this_deployments_or_unknown(tmp_path: Path, generated: bool, owners: str) -> None:
    project, environment, trace = _installation(tmp_path, generated=generated)
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    if owners == "this deployment":
        # The label may name the directory through a symlink; compare physical paths.
        environment["AUDIT_COMPOSE_WORKDIRS"] = f"{project}\n{alias}"
    elif owners == "docker unreachable":
        environment["AUDIT_COMPOSE_WORKDIRS"] = str(tmp_path / "other-deployment")
        environment["AUDIT_DOCKER_PS_EXIT"] = "1"
    result = _run(project, environment, "--database-only")
    assert result.returncode == 0, result.stderr
    assert [call["command"] for call in _calls(trace)] == ["docker", "migrate.sh", "docker"]
    assert len(_guard_queries(trace)) == 1


def test_compose_project_name_selects_the_queried_project_and_adopt_bypasses_the_guard(tmp_path: Path) -> None:
    project, environment, trace = _installation(tmp_path, generated=False)
    with (project / ".env").open("a") as dotenv:
        dotenv.write("COMPOSE_PROJECT_NAME=kip-second\n")
    environment["AUDIT_COMPOSE_WORKDIRS"] = str(tmp_path / "moved-from")
    result = _run(project, environment, "--down")
    assert result.returncode == 2
    assert _queried_project(trace) == "label=com.docker.compose.project=kip-second"
    assert "docker compose -p kip-second down" in result.stderr

    trace.unlink()
    environment["KIP_COMPOSE_ADOPT"] = "1"
    result = _run(project, environment, "--down")
    assert result.returncode == 0, result.stderr
    assert not _guard_queries(trace)
    assert [call["args"][-1] for call in _calls(trace)] == ["down"]


def test_generated_deployment_queries_the_generated_compose_name(tmp_path: Path) -> None:
    project, environment, trace = _installation(tmp_path)
    generated = project / "compose.generated.yaml"
    # Setup copies compose.yaml's `name: kip`, so generated deployments share it.
    assert yaml.safe_load(generated.read_text())["name"] == "kip"
    renamed, count = re.subn(r"^name: kip$", "name: kip-generated", generated.read_text(), flags=re.MULTILINE)
    assert count == 1
    generated.write_text(renamed)
    result = _run(project, environment, "--down")
    assert result.returncode == 0, result.stderr
    assert _queried_project(trace) == "label=com.docker.compose.project=kip-generated"


@pytest.mark.parametrize("entry", ["dev-up.sh", "dev-down.sh", "postgres-tools.sh"])
def test_other_compose_entry_points_share_the_project_guard(tmp_path: Path, entry: str) -> None:
    project, environment, trace = _installation(tmp_path, generated=False)
    if entry == "postgres-tools.sh":
        # backup.sh, restore.sh and ops-report.sh reach Compose through this
        # fallback when no host client is installed.
        command = ["bash", "-c", 'source scripts/common.sh; source scripts/postgres-tools.sh; '
                   'PSQL=/nonexistent/psql postgres_query postgresql://synthetic.invalid/kip "SELECT 1"']
    else:
        command = [str(project / "scripts" / entry)]
    other = tmp_path / "other-deployment"
    environment["AUDIT_COMPOSE_WORKDIRS"] = str(other)
    result = subprocess.run(command, cwd=project, env=environment, capture_output=True, text=True)
    assert result.returncode == 2
    assert f"belongs to another deployment at {other}" in result.stderr
    assert not _calls(trace)

    environment["AUDIT_COMPOSE_WORKDIRS"] = str(project)
    result = subprocess.run(command, cwd=project, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert [call["args"][:1] for call in _calls(trace)] == [["compose"]]


@pytest.mark.parametrize("spelling", ["symlink", "different case", "deleted directory"])
def test_label_directory_is_compared_as_a_file_system_entry(tmp_path: Path, spelling: str) -> None:
    project, environment, trace = _installation(tmp_path, generated=False)
    if spelling == "symlink":
        label = tmp_path / "alias"
        label.symlink_to(project, target_is_directory=True)
        expected = 0
    elif spelling == "different case":
        label = project.with_name(project.name.upper())
        # The same directory on a case-insensitive file system (APFS default),
        # a different one on a case-sensitive file system.
        expected = 0 if label.exists() else 2
    else:
        # A moved or removed deployment: its path no longer exists.
        label = tmp_path / "removed-deployment"
        label.mkdir()
        label.rmdir()
        expected = 2
    environment["AUDIT_COMPOSE_WORKDIRS"] = str(label)
    result = _run(project, environment, "--down")
    assert result.returncode == expected, result.stderr
    assert bool(_calls(trace)) == (expected == 0)


def test_interpolated_compose_name_is_refused_instead_of_queried_literally(tmp_path: Path) -> None:
    project, environment, trace = _installation(tmp_path, generated=False)
    compose = project / "compose.yaml"
    renamed, count = re.subn(r"^name: kip$", "name: ${KIP_STACK:-kip}", compose.read_text(), flags=re.MULTILINE)
    assert count == 1
    compose.write_text(renamed)
    result = _run(project, environment, "--down")
    assert result.returncode == 2
    assert "sets the Compose project name by interpolation" in result.stderr
    assert "COMPOSE_PROJECT_NAME" in result.stderr
    assert not _records(trace)

    environment["COMPOSE_PROJECT_NAME"] = "kip-stack"
    result = _run(project, environment, "--down")
    assert result.returncode == 0, result.stderr
    assert _queried_project(trace) == "label=com.docker.compose.project=kip-stack"


def test_directory_name_fallback_normalises_like_compose(tmp_path: Path) -> None:
    project = tmp_path / "-_My.Kip"
    (project / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/common.sh", project / "scripts/common.sh")
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("KIP_", "COMPOSE_"))}
    environment["KIP_SKIP_DOTENV"] = "1"
    result = subprocess.run(
        ["bash", "-c", "source scripts/common.sh; kip_compose_project_name"],
        cwd=project, env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "mykip\n"


def _closed_port() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return str(probe.getsockname()[1])


_DOCTOR_LABEL = "Compose project not shared with another deployment"


@pytest.mark.parametrize("owners", ["another deployment", "this deployment", "docker unreachable"])
def test_doctor_reports_the_compose_project_check(tmp_path: Path, owners: str) -> None:
    project, environment, trace = _installation(tmp_path, generated=False)
    # Every other required check passes, so the exit status is this check's.
    (project / "AGENTS.md").write_text("# KIP\n")
    (project / "CLAUDE.md").write_text("@AGENTS.md\n")
    (project / "config").mkdir(exist_ok=True)
    (project / "config/kip.toml").write_text("")
    binary = project / ".venv/bin"
    stubs = {
        binary / "node": '#!/bin/sh\n[ "$1" = -p ] && echo 1.0.0\nexit 0\n',
        binary / "kordoc": (
            '#!/bin/sh\n[ "$1" = --version ] && echo 1.0.0\n'
            '[ "$1" = models ] && echo \'[{"group":"ppocr","allReady":true,"models":[{"verified":true}]}]\'\nexit 0\n'
        ),
        project / "scripts/kip": "#!/bin/sh\nexit 0\n",
    }
    for path, body in stubs.items():
        path.write_text(body)
        path.chmod(0o755)
    environment["KIP_SEMANTIC_PORT"] = _closed_port()
    other = tmp_path / "other-deployment"
    other.mkdir()
    if owners == "another deployment":
        environment["AUDIT_COMPOSE_WORKDIRS"] = str(other)
    elif owners == "this deployment":
        environment["AUDIT_COMPOSE_WORKDIRS"] = str(project)
    else:
        environment["AUDIT_COMPOSE_WORKDIRS"] = str(other)
        environment["AUDIT_DOCKER_PS_EXIT"] = "1"
    result = subprocess.run([str(project / "scripts/doctor.sh")], cwd=project, env=environment, capture_output=True, text=True)
    lines = result.stdout.splitlines()
    if owners == "another deployment":
        assert result.returncode == 1, result.stdout
        index = lines.index(f"[required missing] {_DOCTOR_LABEL}")
        assert lines[index + 1] == f'  detail: error: Docker Compose project "kip" belongs to another deployment at {other}'
        assert lines[index + 2].startswith("  fix: a separate deployment: set COMPOSE_PROJECT_NAME=<unique-name>")
        assert "KIP_POSTGRES_PORT" in lines[index + 2]
        assert "KIP_COMPOSE_ADOPT=1 ./scripts/app-up.sh --down" in lines[index + 2]
    elif owners == "this deployment":
        assert result.returncode == 0, result.stdout
        assert f"[ok] {_DOCTOR_LABEL}" in lines
    else:
        # Not checked is not a failure: the container checks report Docker.
        assert result.returncode == 0, result.stdout
        assert f"[not checked] {_DOCTOR_LABEL} (Docker could not be queried)" in lines
    assert len(_guard_queries(trace)) == 1


@pytest.mark.parametrize("shared", [True, False])
def test_ops_report_names_a_shared_compose_project_instead_of_an_unreachable_database(tmp_path: Path, shared: bool) -> None:
    project, environment, trace = _installation(tmp_path, generated=False)
    other = tmp_path / "other-deployment"
    environment.update({
        "AUDIT_COMPOSE_WORKDIRS": str(other if shared else project),
        "PSQL": "/nonexistent/psql", "KIP_CLI": "/usr/bin/true", "KIP_API_PORT": _closed_port(),
    })
    result = subprocess.run([str(project / "scripts/ops-report.sh")], cwd=project, env=environment, capture_output=True, text=True)
    assert result.returncode == 1
    report = json.loads((project / "var/run/ops-report-last.json").read_text())
    compose_execs = [call for call in _calls(trace) if call["args"][:2] == ["compose", "exec"]]
    if shared:
        assert len(_guard_queries(trace)) == 1
        assert not compose_execs
        assert report["checks"]["compose_project"]["error"] == "compose project shared"
        assert report["checks"]["compose_project"]["message"].startswith(
            f'error: Docker Compose project "kip" belongs to another deployment at {other}'
        )
        assert report["checks"]["queue_age"]["error"] == "compose project shared"
        assert report["checks"]["last_sync"]["error"] == "compose project shared"
        assert "[FAIL] compose_project: " in result.stdout
        assert (
            f'OPS-REPORT FAIL: failed-jobs check unavailable (CLI/database unreachable); '
            f'compose project shared: Docker Compose project "kip" belongs to another deployment at {other}; '
            "queue-age check unavailable (compose project shared); last-sync check unavailable (compose project shared)"
        ) in result.stdout
        assert f"belongs to another deployment at {other}" in result.stderr
    else:
        assert len(compose_execs) == 2
        assert "compose_project" not in report["checks"]
        assert report["checks"]["queue_age"]["error"] == "database unreachable"


def test_backup_refuses_a_shared_compose_project_before_creating_a_partial_folder(tmp_path: Path) -> None:
    project, environment, trace = _installation(tmp_path, generated=False)
    other = tmp_path / "other-deployment"
    environment.update({
        "AUDIT_COMPOSE_WORKDIRS": str(other), "PSQL": "/nonexistent/psql", "PG_DUMP": "/nonexistent/pg_dump",
    })
    result = subprocess.run([str(project / "scripts/backup.sh")], cwd=project, env=environment, capture_output=True, text=True)
    assert result.returncode == 2
    assert f"belongs to another deployment at {other}" in result.stderr
    assert not list((project / "var/backups").iterdir())
    assert not _calls(trace)
