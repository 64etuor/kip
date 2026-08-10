from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kip.adapters.repository.memory import MemoryRepository
from kip.api import create_app
from kip.cli import app
from kip.container import build_container
from kip.mcp_server import create_server
from kip.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def _container(tmp_path: Path):
    return build_container(
        Settings(
            project_root=ROOT,
            config_path=tmp_path / "kip.toml",
            raw={
                "search": {"semantic_enabled": False},
                "graph": {"backend": "memory"},
                "sources": {"filesystem": []},
                "interaction": {
                    "enabled": True,
                    "clarification_ttl_seconds": 3600,
                },
                "ontology": {
                    "domain_profile": "empty",
                    "adaptive_discovery": True,
                },
            },
            environment="test",
            workspace="default",
            database_url="memory://",
            cas_path=tmp_path / "cas",
            api_key="test-key",
            admin_key="test-admin",
        ),
        repository=MemoryRepository(),
    )


def test_rest_uses_the_common_interaction_service_and_redacts_fingerprints(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    client = TestClient(create_app(container))
    headers = {"X-KIP-API-Key": "test-key", "X-KIP-Admin-Key": "test-admin"}

    created = client.post(
        "/v1/interactions/clarifications",
        headers=headers,
        json={
            "reason": "scope_selection",
            "prompt": "어느 범위를 검색할까요?",
            "choices": [{"id": "onedrive", "label": "OneDrive"}],
            "allow_freeform": False,
            "preference_key": "default_source_scope",
        },
    )

    assert created.status_code == 200
    question = created.json()["data"]
    answered = client.post(
        f"/v1/interactions/clarifications/{question['id']}/answers",
        headers=headers,
        json={
            "question_id": question["id"],
            "option_ids": ["onedrive"],
            "remember": True,
        },
    )
    preferences = client.get("/v1/interactions/preferences", headers=headers)
    candidate = client.post(
        "/v1/ontology/discovery-candidates",
        headers=headers,
        json={
            "kind": "entity_type",
            "symbol": "contract",
            "label": "계약",
            "definition": "업무상 체결하는 계약을 표현한다.",
            "confirmed": True,
        },
    )

    assert answered.status_code == 200
    assert answered.json()["data"]["preference"]["key"] == "default_source_scope"
    assert preferences.json()["data"][0]["values"] == ["onedrive"]
    assert candidate.status_code == 200
    assert "fingerprint" not in candidate.json()["data"]
    listed = client.get(
        "/v1/admin/ontology/discovery-candidates",
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.json()["data"][0]["id"] == candidate.json()["data"]["id"]


def test_cli_interaction_commands_emit_the_same_versioned_envelope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    container = _container(tmp_path)
    monkeypatch.setattr(
        "kip.cli.build_container",
        lambda settings, load_models=True: container,
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "interaction",
            "clarify",
            "--reason",
            "scope_selection",
            "--prompt",
            "어느 범위를 검색할까요?",
            "--choices-json",
            '[{"id":"onedrive","label":"OneDrive"}]',
            "--no-allow-freeform",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert '"schema_version": "kip.envelope.v1"' in result.stdout
    assert '"schema_version": "kip.clarification.v1"' in result.stdout


def test_cli_interaction_validation_failure_uses_the_error_envelope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    container = _container(tmp_path)
    monkeypatch.setattr(
        "kip.cli.build_container",
        lambda settings, load_models=True: container,
    )

    result = CliRunner().invoke(
        app,
        [
            "interaction",
            "clarify",
            "--reason",
            "unsupported",
            "--prompt",
            "어느 범위를 검색할까요?",
        ],
    )

    assert result.exit_code == 3
    assert '"code": "validation_error"' in result.output


def test_mcp_exposes_the_same_clarification_service(tmp_path: Path, monkeypatch) -> None:
    container = _container(tmp_path)
    monkeypatch.setattr("kip.mcp_server.build_container", lambda: container)
    server = create_server()

    async def invoke() -> tuple[set[str], object]:
        tools = {tool.name for tool in await server.list_tools()}
        result = await server.call_tool(
            "kip_clarify",
            {
                "reason": "scope_selection",
                "prompt": "어느 범위를 검색할까요?",
                "choices_json": '[{"id":"onedrive","label":"OneDrive"}]',
                "allow_freeform": False,
            },
        )
        return tools, result

    tools, result = asyncio.run(invoke())

    assert "kip_clarify" in tools
    payload = json.loads(result[0][0].text)
    assert payload["schema_version"] == "kip.clarification.v1"
