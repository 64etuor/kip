"""Bootstrap writes a new deployment's Compose project name and host ports.

Two agent installs beside a running deployment had to hand-edit `.env`: it
hard-coded ports 5432 and 8080, ignored the exported COMPOSE_PROJECT_NAME and
ports, and shared the `kip` Compose project. A new `.env` now carries what was
exported, or values bootstrap chose after finding another project or a busy
port. A deployment whose own stack exists but whose `.env` is gone stops.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/bootstrap_env.py"

# A docker stand-in: `ps` prints AUDIT_PS rows (project, working dir, ports)
# and `volume ls` prints AUDIT_VOLUMES rows (project, volume), each with its
# own exit status.
DOCKER_STUB = f"""#!{sys.executable}
import json, os, sys
with open(os.environ["AUDIT_DOCKER_TRACE"], "a") as trace:
    trace.write(json.dumps(sys.argv[1:]) + "\\n")
kind = "VOLUMES" if sys.argv[1:3] == ["volume", "ls"] else "PS"
sys.stdout.write(os.environ.get("AUDIT_" + kind, ""))
raise SystemExit(int(os.environ.get("AUDIT_" + kind + "_EXIT", "0")))
"""
CONFIRM = (
    "Confirm these values before ./scripts/app-up.sh. KIP_SEMANTIC_PORT is unchanged: "
    "the machine keeps one model runtime.\n"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bootstrap_env_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dotenv(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


@pytest.fixture()
def deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "Url Test_2"
    project.mkdir()
    shutil.copy2(ROOT / ".env.example", project / ".env.example")
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "docker").write_text(DOCKER_STUB)
    (binary / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("AUDIT_DOCKER_TRACE", str(tmp_path / "docker.jsonl"))
    for name in ("COMPOSE_PROJECT_NAME", "KIP_POSTGRES_PORT", "KIP_API_PORT",
                 "AUDIT_PS", "AUDIT_PS_EXIT", "AUDIT_VOLUMES", "AUDIT_VOLUMES_EXIT"):
        monkeypatch.delenv(name, raising=False)
    return project


def _module(monkeypatch: pytest.MonkeyPatch, busy: set[int]) -> ModuleType:
    module = _load()
    probed: list[int] = []

    def listening(port: int) -> bool:
        probed.append(port)
        return port in busy

    monkeypatch.setattr(module, "_listening", listening)
    module.probed = probed
    return module


def _run(
    project: Path, monkeypatch: pytest.MonkeyPatch, *arguments: str, busy: set[int] = frozenset(),
) -> ModuleType:
    module = _module(monkeypatch, busy)
    assert module.main([str(project), *arguments]) == 0
    return module


def test_exported_project_name_and_ports_are_written_and_nothing_is_probed(
    deployment: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # The environment both real agent installs exported.
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "kipurltest2")
    monkeypatch.setenv("KIP_POSTGRES_PORT", "55433")
    monkeypatch.setenv("KIP_API_PORT", "18081")
    monkeypatch.setenv("AUDIT_PS", "kip\t/srv/kip\t127.0.0.1:5432->5432/tcp\n")

    module = _run(deployment, monkeypatch, "--detect-existing-deployment", busy={5432, 8080})

    values = _dotenv(deployment / ".env")
    assert values["COMPOSE_PROJECT_NAME"] == "kipurltest2"
    assert (values["KIP_POSTGRES_PORT"], values["KIP_API_PORT"]) == ("55433", "18081")
    assert urlsplit(values["KIP_DATABASE_URL"]).port == 55433
    assert urlsplit(values["KIP_BACKUP_DATABASE_URL"]).port == 55433
    assert urlsplit(values["KIP_DATABASE_URL"]).password == values["POSTGRES_PASSWORD"]
    assert values["KIP_SEMANTIC_PORT"] == "7997"
    assert module.probed == []
    assert not (deployment.parent / "docker.jsonl").exists()
    assert capsys.readouterr().err.endswith(
        "Bootstrap wrote the exported values into the new .env:\n"
        "  COMPOSE_PROJECT_NAME=kipurltest2 [exported]\n"
        "  KIP_POSTGRES_PORT=55433 (also in KIP_DATABASE_URL and KIP_BACKUP_DATABASE_URL) [exported]\n"
        "  KIP_API_PORT=18081 [exported]\n"
    )


@pytest.mark.parametrize("value", ["0", "65536", "abc", "5432x", "-1", "\uff15\uff14\uff13\uff12"])
def test_an_exported_port_that_is_not_a_port_stops_before_writing(
    deployment: Path, monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("KIP_POSTGRES_PORT", value)
    with pytest.raises(SystemExit) as stopped:
        _load().main([str(deployment)])
    assert str(stopped.value) == (
        f"error: KIP_POSTGRES_PORT={value!r} is not a port; export an integer from 1 to 65535 "
        "or unset it, then rerun ./scripts/bootstrap.sh"
    )
    assert not (deployment / ".env").exists()


def test_an_exported_project_name_compose_would_refuse_stops_before_writing(
    deployment: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "Kip Second\nKIP_API_PORT=1")
    with pytest.raises(SystemExit, match="is not a Docker Compose project name"):
        _load().main([str(deployment)])
    assert not (deployment / ".env").exists()


def test_another_deployments_containers_make_bootstrap_choose_the_next_free_name(
    deployment: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given the first deployment running from /srv/kip, and same-basename
    # deployments elsewhere that already took the derived name and its -2.
    monkeypatch.setenv("AUDIT_PS", (
        "kip\t/srv/kip\t127.0.0.1:5432->5432/tcp\n"
        "kip-url-test_2-2\t/srv/elsewhere/Url Test_2\t\n"
    ))
    monkeypatch.setenv("AUDIT_VOLUMES", "kip\tkip_kip_pgdata\nkip-url-test_2\tkip-url-test_2_kip_cas\n")

    _run(deployment, monkeypatch, "--detect-existing-deployment", busy={5432})

    values = _dotenv(deployment / ".env")
    assert values["COMPOSE_PROJECT_NAME"] == "kip-url-test_2-3"
    assert (values["KIP_POSTGRES_PORT"], values["KIP_API_PORT"]) == ("55432", "18080")
    assert urlsplit(values["KIP_DATABASE_URL"]).port == 55432
    assert urlsplit(values["KIP_BACKUP_DATABASE_URL"]).port == 55432
    assert values["KIP_SEMANTIC_PORT"] == "7997"
    assert capsys.readouterr().err.endswith(
        'Another KIP deployment is on this machine: Docker Compose project "kip" has containers '
        'from /srv/kip; 127.0.0.1:5432 (KIP_POSTGRES_PORT) is published by Docker Compose project '
        '"kip" at /srv/kip.\n'
        "This .env is new and has no data yet, so bootstrap chose values that do not collide:\n"
        "  COMPOSE_PROJECT_NAME=kip-url-test_2-3 [derived from the deployment directory]\n"
        "  KIP_POSTGRES_PORT=55432 (also in KIP_DATABASE_URL and KIP_BACKUP_DATABASE_URL) "
        "[first free port at or above 55432]\n"
        "  KIP_API_PORT=18080 [first free port at or above 18080]\n" + CONFIRM
    )


def test_busy_ports_without_compose_labels_are_not_called_another_deployment(
    deployment: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given `docker ps` failing while `volume ls` still answers, a busy
    # PostgreSQL port, and an exported API port that is also in use.
    monkeypatch.setenv("AUDIT_PS_EXIT", "1")
    monkeypatch.setenv("AUDIT_VOLUMES", "kip-url-test_2\tkip-url-test_2_kip_cas\n")
    monkeypatch.setenv("KIP_API_PORT", "18090")

    _run(deployment, monkeypatch, "--detect-existing-deployment", busy={5432, 55432, 18090})

    values = _dotenv(deployment / ".env")
    assert values["COMPOSE_PROJECT_NAME"] == "kip-url-test_2-2"
    assert (values["KIP_POSTGRES_PORT"], values["KIP_API_PORT"]) == ("55433", "18090")
    assert urlsplit(values["KIP_DATABASE_URL"]).port == 55433
    error = capsys.readouterr().err
    assert "Another KIP deployment" not in error
    assert error.endswith(
        "The default Docker Compose project or host ports are taken on this machine: "
        "127.0.0.1:5432 (KIP_POSTGRES_PORT) is already in use; "
        "127.0.0.1:18090 (KIP_API_PORT) is already in use.\n"
        "This .env is new and has no data yet, so bootstrap chose values that do not collide:\n"
        "  COMPOSE_PROJECT_NAME=kip-url-test_2-2 [derived from the deployment directory]\n"
        "  KIP_POSTGRES_PORT=55433 (also in KIP_DATABASE_URL and KIP_BACKUP_DATABASE_URL) "
        "[first free port at or above 55432]\n"
        "  KIP_API_PORT=18090 [exported, although already in use]\n" + CONFIRM
    )


def test_this_deployments_own_container_on_the_port_stops_instead_of_choosing(
    deployment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given a running checkout that lost its .env; Compose recorded the
    # directory through a symlink.
    alias = tmp_path / "alias"
    alias.symlink_to(deployment, target_is_directory=True)
    monkeypatch.setenv("AUDIT_PS", f"kip\t{alias}\t127.0.0.1:5432->5432/tcp\n")
    module = _module(monkeypatch, {5432})

    with pytest.raises(SystemExit) as stopped:
        module.main([str(deployment), "--detect-existing-deployment"])

    assert stopped.value.code == 75
    assert not (deployment / ".env").exists()
    assert module.probed == []
    assert capsys.readouterr().err == (
        f"Action required: this deployment's database already exists, but {deployment / '.env'} is missing.\n"
        f'Docker Compose project "kip" has containers created from {deployment}.\n'
        "A new .env would carry a new random database password, which cannot open the existing "
        "database volume.\n"
        f"Restore {deployment / '.env'} from backup, then rerun ./scripts/bootstrap.sh. Nothing was written.\n"
    )


def test_own_stopped_containers_and_volumes_with_a_missing_env_stop_with_the_restore_message(
    deployment: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given a deployment that bootstrap once named kip-url-test_2, now stopped.
    monkeypatch.setenv("AUDIT_PS", f"kip-url-test_2\t{deployment}\t\n")
    monkeypatch.setenv("AUDIT_VOLUMES", (
        "kip-url-test_2\tkip-url-test_2_kip_pgdata\n"
        "kip-url-test_2\tkip-url-test_2_kip_cas\n"
        "kip\tkip_kip_pgdata\n"
    ))

    with pytest.raises(SystemExit) as stopped:
        _module(monkeypatch, set()).main([str(deployment), "--detect-existing-deployment"])

    assert stopped.value.code == 75
    assert not (deployment / ".env").exists()
    error = capsys.readouterr().err
    assert (
        f'Docker Compose project "kip-url-test_2" has containers created from {deployment} and '
        "volumes kip-url-test_2_kip_cas, kip-url-test_2_kip_pgdata.\n"
    ) in error
    assert f"Restore {deployment / '.env'} from backup" in error


def test_another_projects_volumes_alone_make_bootstrap_choose_and_say_they_are_unattributed(
    deployment: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given a first deployment stopped with `app-up.sh --down`: volumes only.
    monkeypatch.setenv("AUDIT_VOLUMES", "kip\tkip_kip_pgdata\nkip\tkip_kip_cas\n")

    _run(deployment, monkeypatch, "--detect-existing-deployment")

    values = _dotenv(deployment / ".env")
    assert values["COMPOSE_PROJECT_NAME"] == "kip-url-test_2"
    assert (values["KIP_POSTGRES_PORT"], values["KIP_API_PORT"]) == ("55432", "18080")
    assert capsys.readouterr().err.endswith(
        'The default Docker Compose project or host ports are taken on this machine: Docker Compose '
        'project "kip" has volumes (kip_kip_cas, kip_kip_pgdata) but no containers, and Docker does '
        "not record which directory created them.\n"
        "This .env is new and has no data yet, so bootstrap chose values that do not collide:\n"
        "  COMPOSE_PROJECT_NAME=kip-url-test_2 [derived from the deployment directory]\n"
        "  KIP_POSTGRES_PORT=55432 (also in KIP_DATABASE_URL and KIP_BACKUP_DATABASE_URL) "
        "[first free port at or above 55432]\n"
        "  KIP_API_PORT=18080 [first free port at or above 18080]\n" + CONFIRM
        + "If those volumes are this deployment's own database (stopped with ./scripts/app-up.sh "
        "--down), do not use these values: restore its .env from backup.\n"
    )


def test_the_project_name_search_gives_up_after_one_hundred_candidates(
    deployment: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = ["kip-url-test_2", *(f"kip-url-test_2-{index}" for index in range(2, 101))]
    monkeypatch.setenv("AUDIT_VOLUMES", "".join(f"{name}\t{name}_kip_pgdata\n" for name in taken))

    with pytest.raises(SystemExit) as stopped:
        _module(monkeypatch, {5432}).main([str(deployment), "--detect-existing-deployment"])

    assert str(stopped.value) == (
        "error: Docker Compose projects kip-url-test_2 through kip-url-test_2-100 all have "
        "containers or volumes on this machine; export COMPOSE_PROJECT_NAME=<unique-name> and "
        "rerun ./scripts/bootstrap.sh"
    )
    assert not (deployment / ".env").exists()


def test_no_conflict_keeps_the_template_defaults(
    deployment: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AUDIT_PS", "other\t/srv/other\t127.0.0.1:6000->6000/tcp\n")
    monkeypatch.setenv("AUDIT_VOLUMES", "other\tother_data\n")

    module = _run(deployment, monkeypatch, "--detect-existing-deployment")

    values = _dotenv(deployment / ".env")
    assert "COMPOSE_PROJECT_NAME" not in values
    assert (values["KIP_POSTGRES_PORT"], values["KIP_API_PORT"]) == ("5432", "8080")
    assert urlsplit(values["KIP_DATABASE_URL"]).port == 5432
    assert module.probed == [5432, 8080]
    error = capsys.readouterr().err
    assert "chose" not in error and "exported" not in error


def test_an_existing_env_is_never_rewritten(
    deployment: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    existing = (
        "KIP_DATABASE_URL=postgresql://kip_owner:change-me@127.0.0.1:5432/kip\n"
        "KIP_POSTGRES_PORT=5432\nKIP_API_PORT=8080\n"
        "KIP_API_DB_PASSWORD=change-me\nKIP_WORKER_DB_PASSWORD=change-me\n"
        "KIP_BACKUP_DB_PASSWORD=test-password\n"
        "KIP_BACKUP_DATABASE_URL=postgresql://kip_backup:test-password@127.0.0.1:5432/kip\n"
    )
    (deployment / ".env").write_text(existing, encoding="utf-8")
    monkeypatch.setenv("KIP_POSTGRES_PORT", "not-a-port")
    monkeypatch.setenv("AUDIT_PS", f"kip\t{deployment}\t127.0.0.1:5432->5432/tcp\n")

    module = _run(deployment, monkeypatch, "--detect-existing-deployment", busy={5432, 8080})

    assert (deployment / ".env").read_text(encoding="utf-8") == existing
    assert module.probed == []
    assert not (deployment.parent / "docker.jsonl").exists()
    assert capsys.readouterr().err == ""


def _bootstrap_project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """bootstrap.sh with the real common.sh and bootstrap_env.py, everything else stubbed."""
    project = tmp_path / "second"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for name in ("bootstrap.sh", "common.sh", "bootstrap_env.py"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    shutil.copy2(ROOT / "compose.yaml", project / "compose.yaml")
    shutil.copy2(ROOT / ".env.example", project / ".env.example")
    (project / "config").mkdir()
    (project / "config/kip.toml").write_text("existing configuration\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "docker").write_text(DOCKER_STUB)
    for path, body in (
        (scripts / "prerequisites.sh", "#!/bin/sh\nexit 0\n"),
        (scripts / "install-kordoc.sh", "#!/bin/sh\nexit 0\n"),
        (binary / "uv", "#!/bin/sh\nexit 0\n"),
        # Only bootstrap_env.py runs for real, with its port probe replaced.
        (project / ".venv/bin/python", (
            "#!/bin/sh\n"
            'case "$1" in */bootstrap_env.py) ;; *) exit 0 ;; esac\n'
            f'exec {sys.executable} -c "$AUDIT_BOOTSTRAP_ENV" "$@"\n'
        )),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    for path in (*binary.iterdir(), *scripts.iterdir(), project / ".venv/bin/python"):
        path.chmod(0o755)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("KIP_", "COMPOSE_", "AUDIT_"))
    }
    environment.update({
        "PATH": f"{binary}:/usr/bin:/bin",
        "KIP_SEMANTIC": "off",
        "AUDIT_DOCKER_TRACE": str(tmp_path / "docker.jsonl"),
        "AUDIT_BOOTSTRAP_ENV": (
            "import importlib.util, os, sys\n"
            "spec = importlib.util.spec_from_file_location('bootstrap_env', sys.argv[1])\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "busy = {int(port) for port in os.environ.get('AUDIT_BUSY_PORTS', '').split()}\n"
            "module._listening = lambda port: port in busy\n"
            "raise SystemExit(module.main(sys.argv[2:]))\n"
        ),
    })
    return project, environment


@pytest.mark.parametrize(
    "case", ["other deployment", "own deployment", "docker unreachable", "exported values"],
)
def test_bootstrap_sh_writes_the_detected_values_into_the_new_env(tmp_path: Path, case: str) -> None:
    project, environment = _bootstrap_project(tmp_path)
    other = tmp_path / "first"
    other.mkdir()
    environment["AUDIT_PS"] = f"kip\t{other}\t127.0.0.1:5432->5432/tcp\n"
    environment["AUDIT_BUSY_PORTS"] = "5432 8080"
    if case == "own deployment":
        environment["AUDIT_PS"] = f"kip\t{project}\t127.0.0.1:5432->5432/tcp\n"
    elif case == "docker unreachable":
        environment.update({"AUDIT_PS_EXIT": "1", "AUDIT_VOLUMES_EXIT": "1", "AUDIT_BUSY_PORTS": ""})
    elif case == "exported values":
        environment.update({"COMPOSE_PROJECT_NAME": "kip-chosen", "KIP_API_PORT": "18081"})

    result = subprocess.run(
        ["bash", str(project / "scripts/bootstrap.sh")], cwd=project, env=environment,
        capture_output=True, text=True,
    )

    if case == "own deployment":
        assert result.returncode == 75
        assert not (project / ".env").exists()
        assert "Action required: this deployment's database already exists" in result.stderr
        return
    assert result.returncode == 0, result.stderr
    values = _dotenv(project / ".env")
    ports = (
        values.get("COMPOSE_PROJECT_NAME"), values["KIP_POSTGRES_PORT"], values["KIP_API_PORT"],
        urlsplit(values["KIP_DATABASE_URL"]).port, urlsplit(values["KIP_BACKUP_DATABASE_URL"]).port,
    )
    if case == "other deployment":
        assert ports == ("kip-second", "55432", "18080", 55432, 55432)
        assert f'Docker Compose project "kip" has containers from {other}' in result.stderr
    elif case == "docker unreachable":
        # Nothing listens on the default ports either, so nothing changes.
        assert ports == (None, "5432", "8080", 5432, 5432)
        assert "chose" not in result.stderr
    else:
        assert ports == ("kip-chosen", "5432", "18081", 5432, 5432)
        assert not (tmp_path / "docker.jsonl").exists()
