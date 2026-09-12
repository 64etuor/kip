"""`configure_logging` must actually run at every entry point.

`JsonFormatter`/`configure_logging` existed and `app.log_level` was read into
`Settings`, but nothing called it: five modules emitted records through
Python's lastResort handler, and `KIP_LOG_LEVEL` changed nothing. Logging
also has to stay off stdout, which carries the CLI's JSON envelope.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from kip.logging import configure_logging
from kip.settings import Settings


@pytest.fixture(autouse=True)
def _restore_root_logging():
    # KIP configures its own `kip` logger and leaves the root logger to the
    # host application, so the fixture restores both.
    root = logging.getLogger("kip")
    handlers = list(root.handlers)
    level = root.level
    yield
    root.handlers = handlers
    root.setLevel(level)


def test_records_are_json_on_stderr_and_never_on_stdout(capsys) -> None:
    configure_logging("DEBUG")

    logging.getLogger("kip.test").debug("projection rebuild started")

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err.strip())
    assert payload["level"] == "DEBUG"
    assert payload["logger"] == "kip.test"
    assert payload["message"] == "projection rebuild started"


def test_the_cli_applies_the_configured_level_and_keeps_stdout_clean(
    test_container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from kip.cli import app

    settings = replace(test_container.settings, log_level="DEBUG")
    monkeypatch.setattr("kip.cli.Settings.load", classmethod(lambda _cls, _config=None: settings))
    monkeypatch.setattr("kip.cli.build_container", lambda settings, load_models=True: test_container)
    logging.getLogger("kip").setLevel(logging.WARNING)

    result = CliRunner().invoke(app, ["capabilities"])

    assert result.exit_code == 0, result.output
    assert logging.getLogger("kip").level == logging.DEBUG
    assert json.loads(result.stdout)["schema_version"] == "kip.envelope.v1"


def test_the_api_and_worker_entry_points_apply_the_configured_level(
    test_container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kip import worker
    from kip.api import create_app

    logging.getLogger("kip").setLevel(logging.WARNING)
    create_app(replace_container_log_level(test_container, "ERROR"))
    assert logging.getLogger("kip").level == logging.ERROR

    settings = replace(test_container.settings, log_level="DEBUG")
    monkeypatch.setattr(worker.Settings, "load", classmethod(lambda _cls, _config=None: settings))
    monkeypatch.setattr(worker, "build_container", lambda settings: test_container)
    monkeypatch.setattr(worker, "run_worker", lambda container: None)

    worker.main()

    assert logging.getLogger("kip").level == logging.DEBUG


def test_the_log_level_environment_variable_reaches_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("KIP_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("KIP_ENV", "test")
    monkeypatch.delenv("KIP_DATABASE_URL", raising=False)
    monkeypatch.delenv("KIP_LOG_LEVEL", raising=False)
    config = tmp_path / "config"
    config.mkdir()
    (config / "kip.toml").write_text('[app]\nlog_level = "WARNING"\n', encoding="utf-8")

    assert Settings.load().log_level == "WARNING"

    monkeypatch.setenv("KIP_LOG_LEVEL", "DEBUG")

    assert Settings.load().log_level == "DEBUG"


def replace_container_log_level(container, level: str):
    container.settings.log_level = level
    return container


def test_the_mcp_entry_point_applies_the_configured_level(monkeypatch, tmp_path: Path) -> None:
    """The MCP process writes its protocol on stdout; its records go to stderr."""
    import kip.mcp_server as mcp_server

    logging.getLogger("kip").setLevel(logging.WARNING)
    container = SimpleNamespace(settings=SimpleNamespace(log_level="DEBUG"))
    monkeypatch.setattr(mcp_server, "build_container", lambda: container)
    monkeypatch.setattr(mcp_server, "create_server", lambda _container: SimpleNamespace(run=lambda: None))

    mcp_server.main()

    assert logging.getLogger("kip").level == logging.DEBUG
