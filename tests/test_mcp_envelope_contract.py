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
from mcp.types import CallToolResult, TextContent

from kip import __version__
from kip.mcp_server import create_server


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
