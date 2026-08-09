from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from kip.cli import app

ROOT = Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    return {
        "KIP_CONFIG": str(ROOT / "config/kip.example.toml"),
        "KIP_DATABASE_URL": "memory://",
        "KIP_PROJECT_ROOT": str(ROOT),
        "KIP_ENV": "test",
    }


def test_ontology_cli_exposes_entity_mining_and_candidate_surfaces() -> None:
    result = CliRunner().invoke(app, ["ontology", "--help"], env=_env())

    assert result.exit_code == 0, result.stdout
    for command in ("entities", "entity-create", "mine", "candidates"):
        assert command in result.stdout


def test_ontology_entity_create_emits_versioned_envelope() -> None:
    result = CliRunner().invoke(
        app,
        [
            "ontology",
            "entity-create",
            "--id",
            "ent_cli",
            "--type",
            "Project",
            "--name",
            "CLI 과제",
            "--alias",
            "과제 CLI",
        ],
        env=_env(),
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "kip.envelope.v1"
    assert payload["data"]["id"] == "ent_cli"
    assert payload["data"]["entity_type"] == "Project"


def test_ontology_mine_fails_closed_when_no_miner_is_configured() -> None:
    result = CliRunner().invoke(
        app,
        ["ontology", "mine", "--unit-id", "unit_missing"],
        env=_env(),
    )

    assert result.exit_code == 3
    payload = json.loads(result.stderr)
    assert payload["error"]["code"] == "validation_error"
    assert "relation miner" in payload["error"]["message"]
