"""MCP tool responses must use the same `kip.envelope.v1` contract as the
CLI and REST edges, both on success and on a `KipError`.

Before this fix, `_json()` returned the raw `model_dump` on success and an
unhandled `KipError` surfaced as a bare FastMCP `ToolError` with only a
message string, losing the typed `code`/`ok=false` shape CLI and REST give
callers for the exact same failure.
"""

from __future__ import annotations

import json

import anyio

from kip.mcp_server import create_server


def test_mcp_success_result_is_wrapped_in_the_envelope(test_container, monkeypatch) -> None:
    monkeypatch.setattr("kip.mcp_server.build_container", lambda: test_container)
    server = create_server()

    async def invoke() -> str:
        result = await server.call_tool("kip_capabilities", {})
        return result[0][0].text

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
        return result[0][0].text

    envelope = json.loads(anyio.run(invoke))

    assert envelope["schema_version"] == "kip.envelope.v1"
    assert envelope["ok"] is False
    assert envelope["data"] is None
    assert envelope["error"]["code"] == "not_found"
