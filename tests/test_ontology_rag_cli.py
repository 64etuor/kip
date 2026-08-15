from __future__ import annotations

import inspect
import json
import tomllib
from pathlib import Path

from typer.testing import CliRunner

from kip.application.ontology_rag import OntologyRagUseCases
from kip.cli import app
from kip.domain.knowledge import RelationMiningRequest

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
    for command in (
        "entities",
        "entity-create",
        "mine",
        "candidates",
        "migrate-materialize",
    ):
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


def test_relation_mining_caps_match_example_config() -> None:
    """Code-level mining caps must stay in sync with config/kip.example.toml.

    Guards against the class default drifting away from the raised
    container/config caps (max_units=200, max_characters=480000,
    max_entity_proposals=128, max_relation_proposals=256).
    """
    with (ROOT / "config/kip.example.toml").open("rb") as handle:
        example_config = tomllib.load(handle)
    mining_config = example_config["models"]["relation_mining"]

    ontology_rag_defaults = {
        name: parameter.default
        for name, parameter in inspect.signature(
            OntologyRagUseCases.__init__
        ).parameters.items()
    }
    assert ontology_rag_defaults["max_mining_units"] == mining_config["max_units"]
    assert (
        ontology_rag_defaults["max_mining_characters"]
        == mining_config["max_characters"]
    )
    assert (
        ontology_rag_defaults["max_entity_proposals"]
        == mining_config["max_entity_proposals"]
    )
    assert (
        ontology_rag_defaults["max_relation_proposals"]
        == mining_config["max_relation_proposals"]
    )

    request_fields = RelationMiningRequest.model_fields
    assert (
        request_fields["max_entity_proposals"].default
        == mining_config["max_entity_proposals"]
    )
    assert (
        request_fields["max_relation_proposals"].default
        == mining_config["max_relation_proposals"]
    )
