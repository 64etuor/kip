#!/usr/bin/env python3
"""A model runtime that serves the wrong model and never says so.

This is the behaviour that cost KIP 3.12.2: Infinity answers
`POST /embeddings` with HTTP 200 for a model name it does not serve and
embeds with whatever is actually loaded, so a deployment whose runtime was
started with different weights silently embedded queries in one space and
searched a projection built in another.

The stub advertises one name on `GET /models` and accepts every name on
`POST /embeddings`, returning well-formed vectors of the right length. A KIP
release that has the served-model guard refuses it; a release that lost the
guard happily indexes and queries against it, which is what
`scripts/e2e-semantic.sh --mode served-model` asserts cannot happen.

Stdlib only, and it binds loopback only.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def build_handler(served_model: str, dimensions: int) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"wrong-model-runtime: {fmt % args}", flush=True)

        def _send(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # do_GET / do_POST are the names BaseHTTPRequestHandler dispatches to.
        def do_GET(self) -> None:
            if self.path.rstrip("/").endswith("/models") or self.path.rstrip("/") == "":
                self._send({"object": "list", "data": [{"id": served_model, "object": "model"}]})
                return
            self._send({"error": "not found"}, status=404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                request = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send({"error": "bad request"}, status=400)
                return
            path = self.path.rstrip("/")
            if path.endswith("/embeddings"):
                inputs = request.get("input") or []
                if isinstance(inputs, str):
                    inputs = [inputs]
                # HTTP 200 for any requested model name: the whole point.
                self._send(
                    {
                        "object": "list",
                        "model": served_model,
                        "data": [
                            {
                                "object": "embedding",
                                "index": index,
                                "embedding": [0.001] * dimensions,
                            }
                            for index, _ in enumerate(inputs)
                        ],
                        "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
                    }
                )
                return
            if path.endswith("/rerank"):
                documents = request.get("documents") or []
                self._send(
                    {
                        "object": "list",
                        "model": served_model,
                        "results": [
                            {"index": index, "relevance_score": 1.0 - index / 100}
                            for index, _ in enumerate(documents)
                        ],
                    }
                )
                return
            self._send({"error": "not found"}, status=404)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--served-model", required=True, help="The name GET /models advertises")
    parser.add_argument("--dimensions", type=int, default=1024)
    arguments = parser.parse_args()
    server = ThreadingHTTPServer(
        ("127.0.0.1", arguments.port), build_handler(arguments.served_model, arguments.dimensions)
    )
    print(f"serving {arguments.served_model} on 127.0.0.1:{arguments.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
