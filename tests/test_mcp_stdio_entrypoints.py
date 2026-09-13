"""The registered MCP entry points serve the protocol from any directory.

An MCP client starts the server from its own working directory. Setup used to
register `bash scripts/mcp.sh` with a relative config, which fails anywhere but
the deployment root. These start the real entry points as subprocesses from an
unrelated directory and speak MCP over stdio: `kip mcp` through the script the
global launcher executes, and the absolute `scripts/mcp.sh` form setup writes.
The memory repository keeps them off every database, and stdout must carry
nothing but JSON-RPC messages.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import IO, Any

import pytest
from mcp.types import LATEST_PROTOCOL_VERSION

from kip import __version__

ROOT = Path(__file__).resolve().parents[1]
_STARTUP_SECONDS = 90


def _environment(tmp_path: Path) -> dict[str, str]:
    config = tmp_path / "kip.toml"
    config.write_text("[search]\nsemantic_enabled = false\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("KIP_") and name not in {"PYTHONPATH", "VIRTUAL_ENV"}
    }
    environment.update({
        "HOME": str(home),
        # Never read the checkout's .env: it names the deployment database.
        "KIP_SKIP_DOTENV": "1",
        "KIP_ENV": "test",
        "KIP_DATABASE_URL": "memory://",
        "KIP_CONFIG": str(config),
        "KIP_CAS_PATH": str(tmp_path / "cas"),
        "KIP_BACKUP_PATH": str(tmp_path / "backups"),
    })
    return environment


def _pump(stream: IO[str], lines: queue.Queue[str | None]) -> None:
    for line in stream:
        lines.put(line)
    lines.put(None)


def _protocol_message(line: str) -> dict[str, Any]:
    message = json.loads(line)
    assert message.get("jsonrpc") == "2.0", f"stdout carried a non-protocol line: {line!r}"
    return message


def _send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()


def _receive(lines: queue.Queue[str | None], request_id: int, deadline: float, stderr: Path) -> dict[str, Any]:
    while True:
        try:
            line = lines.get(timeout=max(deadline - time.monotonic(), 0.1))
        except queue.Empty:
            pytest.fail(f"no answer to request {request_id}; stderr:\n{stderr.read_text()}")
        if line is None:
            pytest.fail(f"server closed stdout before answering {request_id}; stderr:\n{stderr.read_text()}")
        message = _protocol_message(line)
        if message.get("id") == request_id:
            return message


@pytest.mark.parametrize(
    "argv",
    [
        ["bash", str(ROOT / "scripts/kip"), "mcp"],
        ["bash", str(ROOT / "scripts/mcp.sh")],
    ],
    ids=["kip-mcp", "absolute-mcp-sh"],
)
def test_mcp_entry_point_answers_from_an_unrelated_directory(tmp_path: Path, argv: list[str]) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    stderr_path = tmp_path / "stderr.log"
    with stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            argv, cwd=elsewhere, env=_environment(tmp_path), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=stderr, text=True, encoding="utf-8",
        )
        assert process.stdout is not None and process.stdin is not None
        lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=_pump, args=(process.stdout, lines), daemon=True).start()
        try:
            deadline = time.monotonic() + _STARTUP_SECONDS
            _send(process, {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "kip-entrypoint-test", "version": "0"},
                },
            })
            initialized = _receive(lines, 1, deadline, stderr_path)
            _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            _send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            listed = _receive(lines, 2, deadline, stderr_path)
            _send(process, {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "kip_capabilities", "arguments": {}},
            })
            called = _receive(lines, 3, deadline, stderr_path)
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    assert initialized["result"]["serverInfo"]["version"] == __version__
    tool_names = {tool["name"] for tool in listed["result"]["tools"]}
    assert {"kip_capabilities", "kip_search", "kip_read", "kip_xlsx_read"} <= tool_names
    envelope = json.loads(called["result"]["content"][0]["text"])
    assert envelope["schema_version"] == "kip.envelope.v1"
    assert envelope["ok"] is True
    # Whatever the server still wrote before exiting is protocol too.
    while (line := lines.get(timeout=15)) is not None:
        _protocol_message(line)
