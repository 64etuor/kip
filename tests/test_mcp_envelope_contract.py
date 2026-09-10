"""MCP tool responses must use the same `kip.envelope.v1` contract as the
CLI and REST edges, both on success and on a `KipError`.

Before this fix, `_json()` returned the raw `model_dump` on success and an
unhandled `KipError` surfaced as a bare MCP `ToolError` with only a
message string, losing the typed `code`/`ok=false` shape CLI and REST give
callers for the exact same failure.
"""

from __future__ import annotations

import json

import anyio
import pytest
from mcp.types import CallToolResult, TextContent

from kip import __version__
from kip.mcp_server import create_server


def test_answer_filename_binding_is_identical_across_edges(test_container, monkeypatch):
    from fastapi.testclient import TestClient
    from mcp.client import Client
    from typer.testing import CliRunner

    from kip.api import create_app
    from kip.cli import app

    source = test_container.settings.project_root / "source"
    (source / "대상.txt").write_text("승인되지 않은 검토 초안이다.")
    (source / "다른문서.txt").write_text("최종 승인일은 2026년 9월 3일이다. 최종 승인일에 승인했다.")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    query = '"대상.txt" 최종 승인일은 언제인가?'
    monkeypatch.setattr("kip.cli.build_container", lambda settings, load_models=True: test_container)
    monkeypatch.setenv("KIP_WORKSPACE", "default")
    monkeypatch.setenv("KIP_ACL_SCOPES", "workspace:default")
    cli = CliRunner().invoke(app, ["answer", query, "--limit", "1"])
    assert cli.exit_code == 0, cli.output
    with TestClient(create_app(test_container)) as client:
        rest = client.post("/v1/answer", json={"query": query, "limit": 1}, headers={"X-KIP-API-Key": "test-key"})
        assert rest.status_code == 200

    async def invoke():
        async with Client(create_server(test_container)) as client:
            result = await client.call_tool("kip_answer", {"query": query, "limit": 1})
            return json.loads(result.content[0].text)

    envelopes = [json.loads(cli.output), rest.json(), anyio.run(invoke)]
    for envelope in envelopes:
        assert envelope["schema_version"] == "kip.envelope.v1"
        assert envelope["ok"]
        assert envelope["data"]["refused"]
        assert envelope["data"]["refusal_reason"] == "answer_not_present"
        assert envelope["data"]["citations"] == []
        assert envelope["data"]["query"] == query


def test_mcp_discovery_and_read_distinguish_freshness(test_container):
    from mcp.client import Client

    source = test_container.settings.project_root / "source" / "freshness.txt"
    source.write_text("신선도검증 자료")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    async def invoke():
        async with Client(create_server(test_container)) as client:
            found = await client.call_tool("kip_search", {"query": "신선도검증"})
            hit = json.loads(found.content[0].text)["data"][0]
            read = await client.call_tool("kip_read", {"unit_id": hit["unit_id"]})
            pack = await client.call_tool("kip_context", {"query": "신선도검증"})
            return hit, json.loads(read.content[0].text), json.loads(pack.content[0].text)

    hit, read, pack = anyio.run(invoke)
    assert hit["evidence_role"] == "discovery"
    assert hit["source_verification"] == "not_checked"
    assert read["data"]["source_verification"] == "sha256"
    assert pack["data"]["items"][0]["source_verification"] == "stat"
    assert pack["data"]["items"][0]["body_truncated"] is False


@pytest.mark.parametrize("name,arguments", [
    ("kip_search", {"query": "audit", "limit": 0}),
    ("kip_context", {"query": "audit", "max_chars": 1}),
    ("kip_answer", {"query": "   "}),
    ("kip_clarify", {"reason": "other", "prompt": "Choose", "choices_json": "{"}),
    ("kip_remember_preference", {"key": "language", "values": ["ko"], "confirmed": False}),
])
def test_mcp_invalid_domain_input_returns_versioned_error(test_container, monkeypatch, name, arguments):
    from mcp.client import Client

    monkeypatch.setattr("kip.mcp_server.build_container", lambda: test_container)
    server = create_server()

    async def invoke():
        async with Client(server) as client:
            return await client.call_tool(name, arguments)

    result = anyio.run(invoke)
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["schema_version"] == "kip.envelope.v1"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "validation_error"
    assert "input_value" not in payload["error"]["message"]


def test_mcp_discovery_describes_bounds_and_mutation_effects(test_container, monkeypatch):
    from mcp.client import Client

    monkeypatch.setattr("kip.mcp_server.build_container", lambda: test_container)
    server = create_server()

    async def discover():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in anyio.run(discover)}
    assert all(tool.description for tool in tools.values())
    assert all(tool.annotations is not None for tool in tools.values())
    assert tools["kip_search"].input_schema["properties"]["limit"]["minimum"] == 1
    assert tools["kip_search"].input_schema["properties"]["limit"]["maximum"] == 100
    assert tools["kip_read"].annotations.read_only_hint is True
    assert tools["kip_ontology_discovery_review"].annotations.read_only_hint is False
    assert "release" in tools["kip_ontology_discovery_review"].description
    assert "restart" in tools["kip_ontology_discovery_review"].description


def test_mcp_unexpected_error_is_enveloped_without_internal_details(test_container, monkeypatch):
    from mcp.client import Client

    def fail(*args, **kwargs):
        raise RuntimeError("internal-sensitive-detail")

    monkeypatch.setattr("kip.mcp_server.build_container", lambda: test_container)
    monkeypatch.setattr(test_container.application.evidence, "read_unit", fail)
    server = create_server()

    async def invoke():
        async with Client(server) as client:
            return await client.call_tool("kip_read", {"unit_id": "unknown"})

    result = anyio.run(invoke)
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["error"]["code"] == "internal_error"
    assert payload["schema_version"] == "kip.envelope.v1"
    assert "internal-sensitive-detail" not in json.dumps(payload)


def test_mcp_v2_client_discovers_and_calls_server_in_process(test_container, monkeypatch) -> None:
    from mcp.client import Client

    monkeypatch.setattr("kip.mcp_server.build_container", lambda: test_container)
    server = create_server()

    async def invoke() -> tuple[list[str], str, bool, str]:
        async with Client(server) as client:
            tools = await client.list_tools()
            result = await client.call_tool("kip_capabilities", {})
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert client.server_info is not None
            return (
                [tool.name for tool in tools.tools],
                content.text,
                result.is_error,
                client.server_info.version,
            )

    tool_names, raw_envelope, is_error, server_version = anyio.run(invoke)
    envelope = json.loads(raw_envelope)

    assert "kip_capabilities" in tool_names
    assert is_error is False
    assert server_version == __version__
    assert envelope["schema_version"] == "kip.envelope.v1"
    assert envelope["ok"] is True


def test_mcp_success_result_is_wrapped_in_the_envelope(test_container, monkeypatch) -> None:
    monkeypatch.setattr("kip.mcp_server.build_container", lambda: test_container)
    server = create_server()

    async def invoke() -> str:
        result = await server.call_tool("kip_capabilities", {})
        assert isinstance(result, CallToolResult)
        content = result.content[0]
        assert isinstance(content, TextContent)
        return content.text

    envelope = json.loads(anyio.run(invoke))

    assert envelope["schema_version"] == "kip.envelope.v1"
    assert envelope["ok"] is True
    assert envelope["error"] is None
    assert envelope["meta"]["workspace"] == test_container.settings.workspace
    # Capabilities returns a mapping-shaped payload, not a bare list.
    assert isinstance(envelope["data"], dict)


def test_mcp_kip_error_is_wrapped_in_the_envelope_not_a_bare_tool_error(
    test_container,
    monkeypatch,
) -> None:
    monkeypatch.setattr("kip.mcp_server.build_container", lambda: test_container)
    server = create_server()

    async def invoke() -> str:
        result = await server.call_tool("kip_read", {"unit_id": "unit_does_not_exist"})
        assert isinstance(result, CallToolResult)
        content = result.content[0]
        assert isinstance(content, TextContent)
        return content.text

    envelope = json.loads(anyio.run(invoke))

    assert envelope["schema_version"] == "kip.envelope.v1"
    assert envelope["ok"] is False
    assert envelope["data"] is None
    assert envelope["error"]["code"] == "not_found"
