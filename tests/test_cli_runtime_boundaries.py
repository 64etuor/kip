import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kip.cli import app, command_loads_models


def test_migration_does_not_construct_model_clients() -> None:
    assert command_loads_models("migrate") is False
    assert command_loads_models("search") is True


def test_missing_configured_database_emits_versioned_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "production.toml"
    config.write_text(
        '[app]\nenvironment = "production"\n'
        '[database]\nurl_env = "KIP_ABSENT_TEST_DATABASE"\n'
    )
    monkeypatch.delenv("KIP_ABSENT_TEST_DATABASE", raising=False)
    monkeypatch.delenv("KIP_ABSENT_TEST_DATABASE_FILE", raising=False)
    result = CliRunner().invoke(
        app, ["--config", str(config), "--workspace", "selected-workspace", "status"],
    )
    assert result.exit_code == 3
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["schema_version"] == "kip.envelope.v1"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "configuration_error"
    assert payload["meta"]["workspace"] == "selected-workspace"
    assert "KIP_ABSENT_TEST_DATABASE" in payload["error"]["message"]
