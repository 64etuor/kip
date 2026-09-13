#!/usr/bin/env python3
"""Speak MCP over stdio to an installed deployment's server and assert the answers.

`scripts/e2e-install.sh` starts the server exactly the way a user-scope MCP
registration does, `kip mcp` through the global launcher from an unrelated
directory, and hands the command to this probe. The probe sends `initialize`,
`notifications/initialized`, `tools/list` and a `kip_capabilities` call, then
closes stdin and requires that EVERY line the server wrote to stdout was a
JSON-RPC 2.0 message: a single log line or banner on stdout breaks every MCP
client, and it only shows up when the server is started outside a terminal.

Standard library only, like `kip_envelope.py`: it runs under whichever
`python3` is on PATH, never inside the deployment's environment.

    mcp_stdio_probe.py --cwd DIR --expect-version X.Y.Z [--expect-repository postgresql] -- COMMAND [ARG ...]
"""

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import IO, Any

# The server negotiates down to what it supports; the answer is asserted, not assumed.
PROTOCOL_VERSION = "2025-06-18"
REQUIRED_TOOLS = frozenset({"kip_capabilities", "kip_search", "kip_read", "kip_xlsx_read"})


class ProbeFailed(Exception):
    """The server did not answer the way an MCP client needs it to."""


def _pump(stream: IO[str], lines: queue.Queue[str | None]) -> None:
    for line in stream:
        lines.put(line)
    lines.put(None)


def _protocol_message(line: str, seen: list[str]) -> dict[str, Any]:
    seen.append(line)
    try:
        message = json.loads(line)
    except json.JSONDecodeError as error:
        raise ProbeFailed(f"stdout carried a line that is not JSON-RPC: {line!r} ({error})") from error
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise ProbeFailed(f"stdout carried a line that is not a JSON-RPC 2.0 message: {line!r}")
    return message


class Session:
    def __init__(self, process: subprocess.Popen[str], timeout: float, stderr_path: Path) -> None:
        self.process = process
        self.deadline = time.monotonic() + timeout
        self.stderr_path = stderr_path
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.seen: list[str] = []
        assert process.stdout is not None
        threading.Thread(target=_pump, args=(process.stdout, self.lines), daemon=True).start()

    def _stderr(self) -> str:
        return self.stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except BrokenPipeError as error:
            raise ProbeFailed(f"the server exited before {message.get('method')}; stderr:\n{self._stderr()}") from error

    def receive(self, request_id: int) -> dict[str, Any]:
        while True:
            remaining = self.deadline - time.monotonic()
            try:
                line = self.lines.get(timeout=max(remaining, 0.1))
            except queue.Empty as error:
                raise ProbeFailed(f"no answer to request {request_id} in time; stderr:\n{self._stderr()}") from error
            if line is None:
                raise ProbeFailed(f"the server closed stdout before answering request {request_id}; stderr:\n{self._stderr()}")
            message = _protocol_message(line, self.seen)
            if message.get("id") == request_id:
                if "error" in message:
                    raise ProbeFailed(f"request {request_id} answered with a JSON-RPC error: {message['error']!r}")
                return message

    def drain(self) -> None:
        """After stdin closes, whatever the server still writes must be protocol too."""
        while True:
            try:
                line = self.lines.get(timeout=max(self.deadline - time.monotonic(), 0.1))
            except queue.Empty as error:
                raise ProbeFailed("the server did not close stdout after stdin closed") from error
            if line is None:
                return
            _protocol_message(line, self.seen)


def probe(command: list[str], cwd: Path, expect_version: str | None, expect_repository: str | None, timeout: float) -> str:
    with tempfile.TemporaryDirectory(prefix="kip-mcp-probe-") as scratch:
        stderr_path = Path(scratch) / "stderr.log"
        with stderr_path.open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(
                command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                text=True, encoding="utf-8",
            )
            session = Session(process, timeout, stderr_path)
            try:
                session.send({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "kip-e2e-mcp-probe", "version": "0"},
                    },
                })
                initialized = session.receive(1)
                session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                session.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                listed = session.receive(2)
                session.send({
                    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "kip_capabilities", "arguments": {}},
                })
                called = session.receive(3)
                assert process.stdin is not None
                process.stdin.close()
                session.drain()
            finally:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        result = initialized.get("result") or {}
        server = result.get("serverInfo") or {}
        if not result.get("protocolVersion"):
            raise ProbeFailed(f"initialize answered no protocolVersion: {initialized!r}")
        if expect_version is not None and server.get("version") != expect_version:
            raise ProbeFailed(f"serverInfo.version is {server.get('version')!r}, expected {expect_version!r}")
        tools = {tool.get("name") for tool in (listed.get("result") or {}).get("tools") or []}
        missing = sorted(REQUIRED_TOOLS - tools)
        if missing:
            raise ProbeFailed(f"tools/list is missing {missing}; it listed {sorted(tools)}")
        call = called.get("result") or {}
        if call.get("isError"):
            raise ProbeFailed(f"kip_capabilities returned isError: {call!r}")
        content = call.get("content") or []
        if not content or content[0].get("type") != "text":
            raise ProbeFailed(f"kip_capabilities returned no text content: {call!r}")
        try:
            envelope = json.loads(content[0]["text"])
        except json.JSONDecodeError as error:
            raise ProbeFailed(f"kip_capabilities text is not one JSON envelope: {content[0]['text'][:200]!r}") from error
        if envelope.get("schema_version") != "kip.envelope.v1" or envelope.get("ok") is not True:
            raise ProbeFailed(f"kip_capabilities envelope is not an ok kip.envelope.v1: {envelope!r}")
        repository = (envelope.get("data") or {}).get("repository")
        if expect_repository is not None and repository != expect_repository:
            raise ProbeFailed(f"kip_capabilities reports repository {repository!r}, expected {expect_repository!r}")
        return (
            f"mcp: protocol {result['protocolVersion']}, server {server.get('version')}, "
            f"{len(tools)} tools, kip_capabilities ok (repository={repository}), "
            f"{len(session.seen)} stdout lines, all JSON-RPC"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cwd", type=Path, required=True, help="Working directory the server is started from")
    parser.add_argument("--expect-version", default=None)
    parser.add_argument("--expect-repository", default=None)
    parser.add_argument("--timeout", type=float, default=120.0, help="Seconds for the whole exchange")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- COMMAND [ARG ...]")
    arguments = parser.parse_args(argv)
    command = arguments.command[1:] if arguments.command[:1] == ["--"] else arguments.command
    if not command:
        parser.error("a server command is required after --")
    try:
        print(probe(command, arguments.cwd, arguments.expect_version, arguments.expect_repository, arguments.timeout))
    except ProbeFailed as failure:
        print(f"e2e mcp probe failed: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
