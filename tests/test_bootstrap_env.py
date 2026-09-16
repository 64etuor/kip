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
import re
import shutil
import subprocess
import sys
import zipfile
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
        "kip-url-test_2\t/srv/other/Url Test_2\t\n"
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
    # PostgreSQL port, and an exported API port that is also in use. (A volume
    # of this directory's derived name would stop bootstrap: without `ps` it
    # cannot be attributed to another directory.)
    monkeypatch.setenv("AUDIT_PS_EXIT", "1")
    monkeypatch.setenv("AUDIT_VOLUMES", "kip-url-test_20\tkip-url-test_20_kip_cas\n")
    monkeypatch.setenv("KIP_API_PORT", "18090")

    _run(deployment, monkeypatch, "--detect-existing-deployment", busy={5432, 55432, 18090})

    values = _dotenv(deployment / ".env")
    assert values["COMPOSE_PROJECT_NAME"] == "kip-url-test_2"
    assert (values["KIP_POSTGRES_PORT"], values["KIP_API_PORT"]) == ("55433", "18090")
    assert urlsplit(values["KIP_DATABASE_URL"]).port == 55433
    error = capsys.readouterr().err
    assert "Another KIP deployment" not in error
    assert error.endswith(
        "The default Docker Compose project or host ports are taken on this machine: "
        "127.0.0.1:5432 (KIP_POSTGRES_PORT) is already in use; "
        "127.0.0.1:18090 (KIP_API_PORT) is already in use.\n"
        "This .env is new and has no data yet, so bootstrap chose values that do not collide:\n"
        "  COMPOSE_PROJECT_NAME=kip-url-test_2 [derived from the deployment directory]\n"
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


# kip-url-test_2-3 is also the exact name bootstrap gives a directory named
# "url-test_2-3": a stopped deployment there stops this install too, and the
# message says the volumes may be that deployment's.
@pytest.mark.parametrize(
    "project", ["kip-url-test_2", "kip-url-test_2-3"], ids=["own-name", "suffixed-name-shared-with-another-directory"],
)
def test_volumes_of_this_directorys_derived_project_stop_with_the_restore_message(
    deployment: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], project: str,
) -> None:
    # Given a deployment bootstrap once named after this directory, stopped
    # with `app-up.sh --down` (volumes, no containers), whose .env is gone.
    monkeypatch.setenv("AUDIT_VOLUMES", (
        f"{project}\t{project}_kip_pgdata\n{project}\t{project}_kip_cas\nkip\tkip_kip_pgdata\n"
        "kip-url-test_2-101\tkip-url-test_2-101_kip_pgdata\nkip-url-test_20\tkip-url-test_20_kip_pgdata\n"
    ))
    module = _module(monkeypatch, {5432})

    with pytest.raises(SystemExit) as stopped:
        module.main([str(deployment), "--detect-existing-deployment"])

    # Then nothing is written, no port is probed, and the volumes are named.
    assert stopped.value.code == 75
    assert not (deployment / ".env").exists()
    assert module.probed == []
    volumes = f"{project}_kip_cas {project}_kip_pgdata"
    ambiguous = (
        f'A deployment in a directory named "{project.removeprefix("kip-")}" is also named "{project}", '
        "so those volumes may belong to it instead.\n"
    ) if project != "kip-url-test_2" else ""
    assert capsys.readouterr().err == (
        f"Action required: this deployment's database may already exist, but {deployment / '.env'} is missing.\n"
        f'Docker Compose project "{project}" has volumes {project}_kip_cas, {project}_kip_pgdata but no '
        f'containers. Bootstrap names a deployment in {deployment} "kip-url-test_2" (or "kip-url-test_2-N" '
        "when that name is taken), so they may be this deployment's database, stopped with "
        "./scripts/app-up.sh --down.\n"
        + ambiguous
        + "A new .env would carry a new random database password, which cannot open that volume, or "
        "another project name with an empty database. Nothing was written.\n"
        f"- This deployment's database: restore {deployment / '.env'} from backup, then rerun ./scripts/bootstrap.sh.\n"
        "- Another deployment's volumes: export COMPOSE_PROJECT_NAME=<unique-name> (not one of these "
        "names) and rerun ./scripts/bootstrap.sh; it still chooses free ports when the defaults are taken.\n"
        f"- Leftovers with no .env to restore: inspect them with docker volume inspect {volumes}; once their "
        f"data is not needed, remove them with docker volume rm {volumes} and rerun ./scripts/bootstrap.sh.\n"
    )

def test_derived_project_volumes_with_containers_elsewhere_belong_to_that_deployment(
    deployment: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given another directory with the same basename that owns kip-url-test_2.
    monkeypatch.setenv("AUDIT_PS", "kip-url-test_2\t/srv/other/Url Test_2\t\n")
    monkeypatch.setenv("AUDIT_VOLUMES", "kip-url-test_2\tkip-url-test_2_kip_pgdata\n")

    _run(deployment, monkeypatch, "--detect-existing-deployment", busy={5432})

    assert _dotenv(deployment / ".env")["COMPOSE_PROJECT_NAME"] == "kip-url-test_2-2"


def test_an_exported_project_name_alone_still_replaces_a_taken_default_port(
    deployment: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given only COMPOSE_PROJECT_NAME exported, the escape the derived-volume
    # message offers, while another deployment holds 5432.
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "kip-chosen")
    monkeypatch.setenv("AUDIT_VOLUMES", "kip-url-test_2\tkip-url-test_2_kip_pgdata\n")

    module = _run(deployment, monkeypatch, "--detect-existing-deployment", busy={5432})

    values = _dotenv(deployment / ".env")
    assert (values["COMPOSE_PROJECT_NAME"], values["KIP_POSTGRES_PORT"], values["KIP_API_PORT"]) == (
        "kip-chosen", "55432", "18080",
    )
    assert urlsplit(values["KIP_DATABASE_URL"]).port == 55432
    assert module.probed[:2] == [5432, 8080]
    assert not (deployment.parent / "docker.jsonl").exists()
    error = capsys.readouterr().err
    assert "127.0.0.1:5432 (KIP_POSTGRES_PORT) is already in use.\n" in error
    assert "  COMPOSE_PROJECT_NAME=kip-chosen [exported]\n" in error


def test_an_exported_project_name_skips_the_derived_volume_check(
    deployment: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUDIT_VOLUMES", "kip-url-test_2\tkip-url-test_2_kip_pgdata\n")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "kip-chosen")

    _run(deployment, monkeypatch, "--detect-existing-deployment")

    assert _dotenv(deployment / ".env")["COMPOSE_PROJECT_NAME"] == "kip-chosen"
    assert not (deployment.parent / "docker.jsonl").exists()


def test_the_project_name_search_gives_up_after_one_hundred_candidates(
    deployment: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every derived name belongs to a deployment in another directory named
    # like this one; unattributed volumes of these names would stop instead.
    taken = ["kip-url-test_2", *(f"kip-url-test_2-{index}" for index in range(2, 101))]
    monkeypatch.setenv("AUDIT_PS", "".join(f"{name}\t/srv/other/{name}\t\n" for name in taken))
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
    "case", ["other deployment", "own deployment", "own derived volumes", "docker unreachable", "exported values"],
)
def test_bootstrap_sh_writes_the_detected_values_into_the_new_env(tmp_path: Path, case: str) -> None:
    project, environment = _bootstrap_project(tmp_path)
    other = tmp_path / "first"
    other.mkdir()
    environment["AUDIT_PS"] = f"kip\t{other}\t127.0.0.1:5432->5432/tcp\n"
    environment["AUDIT_BUSY_PORTS"] = "5432 8080"
    if case == "own deployment":
        environment["AUDIT_PS"] = f"kip\t{project}\t127.0.0.1:5432->5432/tcp\n"
    elif case == "own derived volumes":
        environment["AUDIT_VOLUMES"] = "kip-second\tkip-second_kip_pgdata\nkip-second\tkip-second_kip_cas\n"
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
    if case == "own derived volumes":
        assert result.returncode == 75
        assert not (project / ".env").exists()
        assert "volumes kip-second_kip_cas, kip-second_kip_pgdata but no containers" in result.stderr
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
        # The exported name and API port are kept; the taken default 5432 is not.
        assert ports == ("kip-chosen", "55432", "18081", 55432, 55432)
        assert not (tmp_path / "docker.jsonl").exists()


# --- KIP_POSTGRES_IMAGE in an existing .env ---------------------------------

OLD_TAG = "pgvector/pgvector:0.8.2-pg18-trixie"
OLD_DIGEST = OLD_TAG + "@sha256:b7337db8fe39d12fe8ecb0003c72680f24479813a744b43154eee6f2eab5a5f3"
EXISTING = (
    "# existing deployment\nPOSTGRES_PASSWORD=test-password\n{line}\n"
    "KIP_API_DB_PASSWORD=test-password\nKIP_WORKER_DB_PASSWORD=test-password\n"
    "KIP_BACKUP_DB_PASSWORD=test-password\n"
    "KIP_BACKUP_DATABASE_URL=postgresql://kip_backup:test-password@127.0.0.1:5432/kip\n"
)


def _current_image(example: Path = ROOT / ".env.example") -> str:
    [value] = [
        line.partition("=")[2] for line in example.read_text(encoding="utf-8").splitlines()
        if line.startswith("KIP_POSTGRES_IMAGE=")
    ]
    return value


def _existing_env(deployment: Path, line: str) -> Path:
    target = deployment / ".env"
    target.write_text(EXISTING.format(line=line), encoding="utf-8")
    target.chmod(0o640)
    return target


def _updated(target: Path, old: str, new: str) -> str:
    return (
        f"Updated KIP_POSTGRES_IMAGE in {target}: {old} -> {new}. The running PostgreSQL container "
        "keeps the old image until the next ./scripts/app-up.sh, which pulls the new one and restarts "
        "PostgreSQL on the same volume; ./scripts/migrate.sh then updates the vector extension.\n"
    )


@pytest.mark.parametrize("flags", [["--refresh-postgres-image"], []], ids=["refresh", "existing-env-path"])
@pytest.mark.parametrize(
    ("line", "old", "expected"),
    [
        (f"KIP_POSTGRES_IMAGE={OLD_TAG}", OLD_TAG, "KIP_POSTGRES_IMAGE={current}"),
        (f"KIP_POSTGRES_IMAGE={OLD_DIGEST}", OLD_DIGEST, "KIP_POSTGRES_IMAGE={current}"),
        (f'export KIP_POSTGRES_IMAGE="{OLD_DIGEST}"  # pinned by KIP', OLD_DIGEST,
         'export KIP_POSTGRES_IMAGE="{current}"  # pinned by KIP'),
    ],
    ids=["tag", "digest", "export-quoted-comment"],
)
def test_an_image_pin_kip_shipped_is_replaced_with_the_current_pin(
    deployment: Path, capsys: pytest.CaptureFixture[str], flags: list[str], line: str, old: str, expected: str,
) -> None:
    # Given an .env an earlier release wrote, with that release's image pin.
    current = _current_image()
    target = _existing_env(deployment, line)

    assert _load().main([str(deployment), *flags]) == 0

    # Then only that line changes, the mode is kept, and the change is printed.
    assert target.read_text(encoding="utf-8") == EXISTING.format(line=expected.format(current=current))
    assert target.stat().st_mode & 0o777 == 0o640
    assert capsys.readouterr().err == _updated(target, old, current)


@pytest.mark.parametrize(
    "line", ["KIP_POSTGRES_IMAGE={current}", "KIP_API_PORT=8080"], ids=["already-current", "no-image-line"],
)
def test_a_current_or_absent_image_pin_is_left_alone_silently(
    deployment: Path, capsys: pytest.CaptureFixture[str], line: str,
) -> None:
    target = _existing_env(deployment, line.format(current=_current_image()))
    before = target.read_bytes()

    assert _load().main([str(deployment), "--refresh-postgres-image"]) == 0

    assert target.read_bytes() == before
    assert capsys.readouterr().err == ""


def test_a_custom_image_is_kept_with_a_warning_naming_the_current_pin(
    deployment: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    custom = "registry.example.test/mirror/pgvector:0.8.2-pg18-trixie"
    current = _current_image()
    target = _existing_env(deployment, f"KIP_POSTGRES_IMAGE={custom}")
    before = target.read_bytes()

    assert _load().main([str(deployment), "--refresh-postgres-image"]) == 0

    assert target.read_bytes() == before
    assert capsys.readouterr().err == (
        f"Warning: {target} sets KIP_POSTGRES_IMAGE={custom}, which is not an image KIP shipped, so it "
        f"was left unchanged. This release pins {current}. To use it, set KIP_POSTGRES_IMAGE={current} "
        "in .env (or the same image from your registry), then run ./scripts/app-up.sh and "
        "./scripts/migrate.sh.\n"
    )


def test_dry_run_reports_the_change_and_writes_nothing(
    deployment: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    current = _current_image()
    target = _existing_env(deployment, f"KIP_POSTGRES_IMAGE={OLD_DIGEST}")
    before = target.read_bytes()

    assert _load().main([str(deployment), "--refresh-postgres-image", "--dry-run"]) == 0

    assert target.read_bytes() == before
    assert capsys.readouterr().err == (
        f"Would update KIP_POSTGRES_IMAGE in {target}: {OLD_DIGEST} -> {current}. Nothing was written.\n"
    )
    with pytest.raises(SystemExit) as refused:
        _load().main([str(deployment), "--dry-run"])
    assert refused.value.code == 2


def test_a_package_zip_supplies_the_next_pin_and_the_installed_pin_counts_as_shipped(
    deployment: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given a deployment on this release's pin previewing a later package.
    installed = _current_image()
    future = "pgvector/pgvector:9.9.9-pg18-trixie@sha256:" + "0" * 64
    archive = tmp_path / "kip-9.9.9.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("kip-9.9.9/.env.example", f"KIP_ENV=development\nKIP_POSTGRES_IMAGE={future}\n")
    target = _existing_env(deployment, f"KIP_POSTGRES_IMAGE={installed}")
    before = target.read_bytes()

    assert _load().main([str(deployment), "--refresh-postgres-image", "--dry-run", "--example", str(archive)]) == 0

    assert target.read_bytes() == before
    assert capsys.readouterr().err == (
        f"Would update KIP_POSTGRES_IMAGE in {target}: {installed} -> {future}. Nothing was written.\n"
    )


def test_every_image_pin_in_git_history_is_known_as_shipped() -> None:
    # The PostgreSQL images earlier releases pinned in .env.example and the
    # Compose files; a pin bump that forgets the outgoing value would strand
    # upgraded deployments on it.
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("needs git and this checkout's history; a package archive has neither")
    shallow = subprocess.run(
        [git, "rev-parse", "--is-shallow-repository"], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if shallow.returncode != 0 or shallow.stdout.strip() != "false":
        pytest.skip("needs the full git history; a shallow clone (fetch-depth 1) lacks the earlier pins")
    history = subprocess.run(
        [git, "log", "-p", "--format=", "--", ".env.example", "compose.yaml", "compose.production.yaml",
         "deploy/compose.roles.yaml"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if history.returncode != 0 or not history.stdout:
        pytest.skip("the git history of the image pins could not be read")
    values = set(re.findall(r"pgvector/pgvector:[^\s}\"']+", history.stdout))
    assert OLD_TAG in values
    assert values - {_current_image()} <= _load().SHIPPED_POSTGRES_IMAGES


def test_bootstrap_sh_refreshes_the_image_pin_of_an_existing_env(tmp_path: Path) -> None:
    project, environment = _bootstrap_project(tmp_path)
    target = project / ".env"
    target.write_text(EXISTING.format(line=f"KIP_POSTGRES_IMAGE={OLD_TAG}"), encoding="utf-8")

    result = subprocess.run(
        ["bash", str(project / "scripts/bootstrap.sh")], cwd=project, env=environment,
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert _dotenv(target)["KIP_POSTGRES_IMAGE"] == _current_image()
    assert f"Updated KIP_POSTGRES_IMAGE in {target}: {OLD_TAG} -> " in result.stderr


def _upgrade_finish_project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """upgrade.sh --finish with the real bootstrap_env.py and bootstrap.sh; prerequisites, migrate and doctor stubbed."""
    project = tmp_path / "deployment"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for name in ("upgrade.sh", "runtime-path.sh", "bootstrap_env.py", "bootstrap.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    shutil.copy2(ROOT / ".env.example", project / ".env.example")
    for path, body in (
        (scripts / "prerequisites.sh", "#!/bin/sh\necho prerequisites-stub\n"),
        (scripts / "migrate.sh", "#!/bin/sh\necho migrate-stub\n"),
        (scripts / "kip", "#!/bin/sh\nexit 0\n"),
        (project / ".venv/bin/python", f'#!/bin/sh\nexec {sys.executable} "$@"\n'),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        path.chmod(0o755)
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("KIP_", "COMPOSE_", "AUDIT_"))
    }
    environment.update({"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"})
    return project, environment


def test_upgrade_finish_refreshes_the_pin_and_check_only_reports(tmp_path: Path) -> None:
    project, environment = _upgrade_finish_project(tmp_path)
    target = _existing_env(project, f"KIP_POSTGRES_IMAGE={OLD_DIGEST}")
    before = target.read_bytes()
    current = _current_image()

    checked = subprocess.run(
        ["bash", str(project / "scripts/upgrade.sh"), "--finish", "--check"], cwd=project, env=environment,
        capture_output=True, text=True,
    )
    assert checked.returncode == 0, checked.stderr
    assert target.read_bytes() == before
    assert f"Would update KIP_POSTGRES_IMAGE in {target}: {OLD_DIGEST} -> {current}. Nothing was written." in checked.stderr
    # The real bootstrap.sh --check ran (prerequisites only) and wrote nothing.
    assert "prerequisites-stub" in checked.stdout
    assert "migrate-stub" not in checked.stdout

    # A full bootstrap.sh would sync the locked environment. Stubbing it is safe
    # here: finish refreshes the pin itself before bootstrap runs, and
    # test_bootstrap_sh_refreshes_the_image_pin_of_an_existing_env covers
    # bootstrap.sh's own refresh, which is then a no-op.
    (project / "scripts/bootstrap.sh").write_text("#!/bin/sh\necho bootstrap-stub\n")
    finished = subprocess.run(
        ["bash", str(project / "scripts/upgrade.sh"), "--finish"], cwd=project, env=environment,
        capture_output=True, text=True,
    )
    assert finished.returncode == 0, finished.stderr
    assert target.read_text(encoding="utf-8") == EXISTING.format(line=f"KIP_POSTGRES_IMAGE={current}")
    assert target.stat().st_mode & 0o777 == 0o640
    assert _updated(target, OLD_DIGEST, current) in finished.stderr
    assert "Upgrade complete" in finished.stdout
    assert finished.stdout.endswith(
        "KIP_POSTGRES_IMAGE changed in .env: the running PostgreSQL container keeps the old image until "
        "./scripts/app-up.sh (or --database-only) recreates it; then run ./scripts/migrate.sh to update the "
        "vector extension.\n"
    )


def test_a_rewrite_keeps_crlf_line_endings_and_the_owner(
    deployment: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given a CRLF .env without the application role logins and with an old pin.
    target = deployment / ".env"
    target.write_bytes(
        b"POSTGRES_PASSWORD=test-password\r\nKIP_POSTGRES_IMAGE=" + OLD_TAG.encode()
        + b"\r\nKIP_DATABASE_URL=postgresql://kip_owner:test-password@127.0.0.1:5432/kip\r\n"
    )
    status = target.stat()
    owners: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: owners.append((uid, gid)))

    assert _load().main([str(deployment)]) == 0

    data = target.read_bytes()
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert f"KIP_POSTGRES_IMAGE={_current_image()}\r\n".encode() in data
    assert b"KIP_API_DB_PASSWORD=" in data and b"KIP_BACKUP_DATABASE_URL=" in data
    # Both rewrites asked to keep the original owner and group.
    assert owners == [(status.st_uid, status.st_gid)] * 2
    assert "Updated KIP_POSTGRES_IMAGE" in capsys.readouterr().err


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can write a read-only directory")
@pytest.mark.parametrize("credentials", [True, False], ids=["pin-only", "pin-and-logins"])
def test_an_unwritable_deployment_directory_warns_instead_of_failing(
    deployment: Path, capsys: pytest.CaptureFixture[str], credentials: bool,
) -> None:
    target = _existing_env(deployment, f"KIP_POSTGRES_IMAGE={OLD_TAG}")
    if not credentials:
        target.write_text(f"POSTGRES_PASSWORD=test-password\nKIP_POSTGRES_IMAGE={OLD_TAG}\n", encoding="utf-8")
    before = target.read_bytes()
    deployment.chmod(0o555)
    try:
        code = _load().main([str(deployment)])
    finally:
        deployment.chmod(0o755)

    assert code == 0
    assert target.read_bytes() == before
    error = capsys.readouterr().err
    assert error.endswith(
        f"Warning: could not update KIP_POSTGRES_IMAGE in {target} (Permission denied); "
        f"set KIP_POSTGRES_IMAGE={_current_image()} there by hand.\n"
    )
    if not credentials:
        assert error.startswith(
            "Warning: could not add KIP_API_DB_PASSWORD, KIP_WORKER_DB_PASSWORD, KIP_BACKUP_DB_PASSWORD "
            f"to {target} (Permission denied); add them by hand.\n"
        )
