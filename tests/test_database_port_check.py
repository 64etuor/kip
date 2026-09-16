"""Host commands refuse a loopback database URL on another deployment's port.

3.15.3 refused it only in `app-up.sh --database-only`; migrate, the CLI and
MCP wrappers still used a mismatched KIP_DATABASE_URL silently. The check now
lives in scripts/common.sh as pure bash, split the way urllib's urlsplit
splits a URL, so the wrappers pay no interpreter start for it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
OWNER = "postgresql://kip_owner:test-password@127.0.0.1:{port}/kip"


def _project(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for name in ("common.sh", "runtime-path.sh", "load_dotenv.py", "migrate.sh", "kip", "mcp.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    trace = tmp_path / "python.log"
    python = project / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    # Only the dotenv loader runs for real; every other start is recorded.
    python.write_text(
        "#!/bin/sh\n"
        f'case "$1" in */load_dotenv.py) exec {sys.executable} "$@" ;; esac\n'
        f'printf "%s\\n" "$*" >> {trace}\n'
    )
    python.chmod(0o755)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("KIP_", "COMPOSE_", "POSTGRES_"))
    }
    environment.update({"PATH": "/usr/bin:/bin", "KIP_DATABASE_URL": OWNER.format(port=5432)})
    return project, environment, trace


def _run(project: Path, environment: dict[str, str], *command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(project / "scripts" / command[0]), *command[1:]], cwd=project, env=environment,
        capture_output=True, text=True,
    )


def _starts(trace: Path) -> list[str]:
    return trace.read_text().splitlines() if trace.exists() else []


@pytest.mark.parametrize(
    "command",
    [("migrate.sh",), ("kip", "search", "query"), ("kip", "sync", "run", "--source", "docs"), ("mcp.sh",),
     ("kip", "mcp")],
)
def test_host_commands_refuse_a_loopback_url_on_another_port(tmp_path: Path, command: tuple[str, ...]) -> None:
    # Given a second deployment publishing 55432 whose URL still names 5432.
    project, environment, trace = _project(tmp_path)
    environment["KIP_POSTGRES_PORT"] = "55432"

    result = _run(project, environment, *command)

    # Then nothing starts, stdout (an MCP client's protocol stream) stays empty,
    # and the message names the setting and the override.
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == (
        "error: KIP_DATABASE_URL uses port 5432, but this deployment publishes PostgreSQL on 55432 "
        "(KIP_POSTGRES_PORT); another deployment may own port 5432. Set the same port in "
        "KIP_DATABASE_URL and KIP_BACKUP_DATABASE_URL in .env. If this URL deliberately names a "
        "separate local PostgreSQL, set KIP_DATABASE_PORT_CHECK=off.\n"
    )
    assert _starts(trace) == []


@pytest.mark.parametrize("case", ["override in .env", "external host", "generated deployment", "matching port"])
def test_migrate_passes_when_the_url_is_not_another_deployments(tmp_path: Path, case: str) -> None:
    project, environment, trace = _project(tmp_path)
    environment["KIP_POSTGRES_PORT"] = "55432"
    if case == "override in .env":
        (project / ".env").write_text("KIP_DATABASE_PORT_CHECK=off\n")
    elif case == "external host":
        environment["KIP_DATABASE_URL"] = "postgresql://kip_owner:test-password@db.example.test:5432/kip"
    elif case == "generated deployment":
        # setup_compose.py checks the generated deployment's URL itself.
        (project / "compose.generated.yaml").write_text("name: kip\n")
    else:
        environment["KIP_DATABASE_URL"] = OWNER.format(port=55432)

    result = _run(project, environment, "migrate.sh")

    assert result.returncode == 0, result.stderr
    assert _starts(trace) == ["-m kip.cli migrate"]


@pytest.mark.parametrize("command", ["doctor", "setup", "version", "--help"])
def test_kip_wrapper_lets_doctor_and_setup_run_to_report_or_repair(tmp_path: Path, command: str) -> None:
    project, environment, trace = _project(tmp_path)
    environment["KIP_POSTGRES_PORT"] = "55432"

    result = _run(project, environment, "kip", command)

    assert result.returncode == 0, result.stderr
    assert _starts(trace) == [f"-m kip.cli {command}"]


@pytest.mark.parametrize("command", [("migrate.sh",), ("kip", "migrate"), ("mcp.sh",)])
def test_an_undeclared_published_port_is_not_compared(tmp_path: Path, command: tuple[str, ...]) -> None:
    # Given a throwaway database (e2e scripts, the verify gate) reached by a bare
    # URL on an arbitrary loopback port, with KIP_POSTGRES_PORT unset or empty.
    project, environment, trace = _project(tmp_path)
    environment["KIP_DATABASE_URL"] = OWNER.format(port=57424)
    environment["KIP_BACKUP_DATABASE_URL"] = "postgresql://kip_backup:test-password@localhost:57424/kip"

    result = _run(project, environment, *command)
    environment["KIP_POSTGRES_PORT"] = ""
    empty = _run(project, environment, *command)

    assert (result.returncode, empty.returncode) == (0, 0), result.stderr + empty.stderr
    assert len(_starts(trace)) == 2


@pytest.mark.parametrize(
    ("arguments", "guarded"),
    [
        (("--config", "/srv/kip/config/kip.toml", "doctor"), False),
        (("--workspace=acme", "setup", "inspect"), False),
        (("--acl-scope", "team", "--role", "admin", "search", "query"), True),
        # A root option's value is not the subcommand.
        (("--workspace", "doctor", "search", "query"), True),
        (("--roles=admin", "sync", "run", "--help"), False),
        (("--principal", "principal_local", "--help"), False),
        # After "--" a --help is a search query, not a request for help.
        (("search", "--", "--help"), True),
        (("version",), False),
        ((), False),
    ],
    ids=["option-doctor", "opt=value-setup", "options-search", "value-named-doctor", "help-after-subcommand",
         "help-after-option", "help-after-dashes", "version", "no-arguments"],
)
def test_kip_wrapper_finds_the_subcommand_after_root_options(
    tmp_path: Path, arguments: tuple[str, ...], guarded: bool
) -> None:
    project, environment, trace = _project(tmp_path)
    environment["KIP_POSTGRES_PORT"] = "55432"

    result = _run(project, environment, "kip", *arguments)

    if guarded:
        assert result.returncode == 2
        assert "KIP_DATABASE_PORT_CHECK=off" in result.stderr
        assert _starts(trace) == []
    else:
        assert result.returncode == 0, result.stderr
        assert _starts(trace) == [" ".join(("-m kip.cli", *arguments))]


def test_a_backup_url_on_another_port_is_refused_too(tmp_path: Path) -> None:
    project, environment, trace = _project(tmp_path)
    environment["KIP_POSTGRES_PORT"] = "5432"
    environment["KIP_BACKUP_DATABASE_URL"] = "postgresql://kip_backup:test-password@localhost:55432/kip"

    result = _run(project, environment, "mcp.sh")

    assert result.returncode == 2
    assert result.stderr.startswith("error: KIP_BACKUP_DATABASE_URL uses port 55432, but this deployment publishes PostgreSQL on 5432")
    assert _starts(trace) == []


def test_a_published_port_that_is_not_a_number_is_refused(tmp_path: Path) -> None:
    project, environment, _ = _project(tmp_path)
    environment["KIP_POSTGRES_PORT"] = "5432x"

    result = _run(project, environment, "migrate.sh")

    assert result.returncode == 2
    assert result.stderr == "error: KIP_POSTGRES_PORT='5432x' is not a port number\n"


URLS = [
    OWNER.format(port=5432), OWNER.format(port=55432), "postgresql://u:test-password@LOCALHOST:55432/kip",
    "postgresql://u:test-password@localhost/kip", "postgresql://u:test-password@[::1]:55432/kip",
    "postgresql://u:test-password@[::1]/kip", "postgresql://u:test-password@[::1]x55432/kip",
    "postgresql://u%40x:test-password@127.0.0.1:55432?sslmode=disable", "postgresql://u:test-password@x@127.0.0.1:55432/kip",
    "postgresql://u:test-password@db.example.test:55432/kip", "postgresql://u:test-password@127.0.0.1:/kip",
    "postgresql://u:test-password@127.0.0.1:abc/kip", "postgresql://u:test-password@127.0.0.1:99999/kip",
    "postgresql://u:test-password@127.0.0.1:05432/kip", "postgresql://u:test-password@127.0.0.1:5432:1/kip",
    "postgresql://127.0.0.1:55432", "postgresql://u:test-password@127.0.0.1:55432#fragment",
    "postgresql://u:test-password@[127.0.0.1]:55432/kip", "postgresql://u:test-password@[::1:55432/kip",
    "postgresql://u:test-password@127.0.0.2:55432/kip", "sqlite:///srv/kip/kip.db", "",
    "postgresql://u:test-password@127.0.0.1:0/kip", "postgresql://u:test-password@127.0.0.1:00000/kip",
    "postgresql://u:test-password@127.0.0.1:005432/kip",
    "postgresql://u:test-password@localhost:65536/kip", "postgresql://u:test-password@db.example.test:99999/kip",
    "postgresql://u:test-password@[::1]:99999/kip",
]


def _urlsplit_verdict(published: int, url: str) -> int:
    """The rule doctor's database_url_port shares: urlsplit decides the host, a
    loopback URL with an invalid port (not 1-65535) is refused, and a URL
    urlsplit cannot split (a malformed [host]) is not judged."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return 0
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return 0
    try:
        port = parts.port
    except ValueError:
        return 2
    return 2 if (port or 5432) != published else 0


@pytest.mark.parametrize("published", [5432, 55432])
def test_the_bash_parser_agrees_with_urlsplit(tmp_path: Path, published: int) -> None:
    project, environment, _ = _project(tmp_path)
    environment["KIP_POSTGRES_PORT"] = str(published)
    verdicts = {}
    for url in URLS:
        environment["KIP_DATABASE_URL"] = url
        result = subprocess.run(
            ["bash", "-c", "source scripts/common.sh; kip_database_port_check"],
            cwd=project, env=environment, capture_output=True, text=True,
        )
        verdicts[url] = result.returncode
    assert verdicts == {url: _urlsplit_verdict(published, url) for url in URLS}


@pytest.mark.parametrize(
    ("port", "shown"),
    [("99999", "99999"), ("abc", "abc"), ("5432:1", "5432:1"), ("", None), ("0", None), ("00000", None)],
    ids=["out-of-range", "letters", "two-colons", "empty-means-5432", "zero-means-5432", "zeros-mean-5432"],
)
def test_an_invalid_loopback_port_is_refused_and_an_external_one_is_not_judged(
    tmp_path: Path, port: str, shown: str | None
) -> None:
    project, environment, _ = _project(tmp_path)
    environment["KIP_POSTGRES_PORT"] = "5432"
    environment["KIP_DATABASE_URL"] = f"postgresql://kip_owner:test-password@localhost:{port}/kip"

    result = _run(project, environment, "migrate.sh")

    if shown is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode == 2
        assert result.stderr == (
            f"error: KIP_DATABASE_URL has an invalid port '{shown}' (use 1-65535). Set the same port as "
            "KIP_POSTGRES_PORT (5432) in KIP_DATABASE_URL and KIP_BACKUP_DATABASE_URL in .env. If this URL "
            "deliberately names a separate local PostgreSQL, set KIP_DATABASE_PORT_CHECK=off.\n"
        )
        environment["KIP_DATABASE_URL"] = f"postgresql://kip_owner:test-password@db.example.test:{port}/kip"
        assert _run(project, environment, "migrate.sh").returncode == 0


def test_python_cli_migrate_refuses_a_loopback_url_on_another_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from kip.cli import app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KIP_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("KIP_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("KIP_POSTGRES_PORT", "55432")
    monkeypatch.setenv("KIP_DATABASE_URL", OWNER.format(port=5432))

    result = CliRunner().invoke(app, ["migrate"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "KIP_DATABASE_URL uses port 5432" in result.stderr
    assert "KIP_POSTGRES_PORT" in result.stderr


def test_python_mcp_entrypoint_refuses_a_loopback_url_on_another_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kip.mcp_server import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KIP_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("KIP_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("KIP_POSTGRES_PORT", "55432")
    monkeypatch.setenv("KIP_DATABASE_URL", OWNER.format(port=5432))

    with pytest.raises(SystemExit) as raised:
        main()

    assert raised.value.code == 2


def test_an_empty_url_variable_is_checked_through_its_file(tmp_path: Path) -> None:
    project, environment, trace = _project(tmp_path)
    environment["KIP_POSTGRES_PORT"] = "55432"
    secret = tmp_path / "database-url"
    secret.write_text(OWNER.format(port=5432) + "\n")
    del environment["KIP_DATABASE_URL"]
    environment["KIP_DATABASE_URL_FILE"] = str(secret)

    result = _run(project, environment, "migrate.sh")

    assert result.returncode == 2
    assert result.stderr.startswith("error: KIP_DATABASE_URL uses port 5432, but this deployment publishes PostgreSQL on 55432")
    assert _starts(trace) == []


def _restore_project(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """restore.sh and the real scripts/kip wrapper; PostgreSQL tools, backup_artifacts.py and kip.cli stubbed."""
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for name in ("restore.sh", "common.sh", "runtime-path.sh", "load_dotenv.py", "kip", "secret_value.py"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    trace = tmp_path / "calls.log"
    (scripts / "postgres-tools.sh").write_text(
        "postgres_query() { case \"$2\" in *rolbypassrls*) echo t ;; *count*) echo 0 ;; esac; }\n"
        "postgres_restore() { :; }\n"
        "postgres_query_file() { printf '{}' > \"$3\"; }\n"
    )
    python = project / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(
        "#!/bin/sh\n"
        f'case "$1" in */load_dotenv.py|*/secret_value.py) exec {sys.executable} "$@" ;; */backup_artifacts.py) echo "{{}}"; exit 0 ;; esac\n'
        f'printf "%s KIP_DATABASE_URL=%s\\n" "$*" "$KIP_DATABASE_URL" >> {trace}\n'
        "echo '{}'\n"
    )
    python.chmod(0o755)
    backup = tmp_path / "backup"
    backup.mkdir()
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("KIP_", "COMPOSE_", "POSTGRES_"))
    }
    environment.update({
        "PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "src"),
        "KIP_POSTGRES_PORT": "5432", "KIP_DATABASE_URL": OWNER.format(port=5432),
        "KIP_RESTORE_DATABASE_URL": "postgresql://kip_owner:test-password@127.0.0.1:55999/kip_restore",
        "KIP_RESTORE_CONFIRM": "YES", "KIP_RESTORE_CAS_PATH": str(tmp_path / "restore-cas"),
        "KIP_RESTORE_EVIDENCE_PATH": str(tmp_path / "evidence"),
    })
    return project, environment, trace


def test_restore_into_a_separate_server_on_another_port_is_not_refused(tmp_path: Path) -> None:
    # Given a deployment on 5432 restoring into an isolated, empty database on
    # 127.0.0.1:55999, while KIP_POSTGRES_PORT stays exported from its .env.
    project, environment, trace = _restore_project(tmp_path)

    result = subprocess.run(
        [str(project / "scripts/restore.sh"), str(tmp_path / "backup")], cwd=project, env=environment,
        capture_output=True, text=True,
    )

    # Then restore finishes, and every kip call in its subshell reached the CLI
    # with the restore target instead of being refused by the port guard.
    assert result.returncode == 0, result.stderr
    assert "uses port" not in result.stderr
    target = "KIP_DATABASE_URL=postgresql://kip_owner:test-password@127.0.0.1:55999/kip_restore"
    assert trace.read_text().splitlines() == [
        f"-m kip.cli {command} {target}"
        for command in ("migrate", "projection rebuild --name lexical", "projection verify --name lexical",
                        "projection verify --name graph", "status")
    ]


def test_restore_still_needs_a_target_other_than_the_deployment_database(tmp_path: Path) -> None:
    project, environment, trace = _restore_project(tmp_path)
    environment["KIP_RESTORE_DATABASE_URL"] = environment["KIP_DATABASE_URL"]

    result = subprocess.run(
        [str(project / "scripts/restore.sh"), str(tmp_path / "backup")], cwd=project, env=environment,
        capture_output=True, text=True,
    )

    assert result.returncode == 2
    assert result.stderr == "restore target must differ from KIP_DATABASE_URL\n"
    assert not trace.exists()


def test_the_same_kip_call_outside_restore_is_still_refused(tmp_path: Path) -> None:
    # The override belongs to restore's subshell: a deployment whose own URL
    # names another port is refused as before.
    project, environment, trace = _restore_project(tmp_path)
    environment["KIP_DATABASE_URL"] = environment.pop("KIP_RESTORE_DATABASE_URL")

    result = subprocess.run(
        [str(project / "scripts/kip"), "migrate"], cwd=project, env=environment, capture_output=True, text=True,
    )

    assert result.returncode == 2
    assert "KIP_DATABASE_URL uses port 55999, but this deployment publishes PostgreSQL on 5432" in result.stderr
    assert not trace.exists()
