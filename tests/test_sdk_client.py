from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk/python"))

from kip_client import KipClient


def test_sdk_does_not_send_untrusted_identity_headers_by_default() -> None:
    client = KipClient(api_key="api-key")

    headers = client._headers()

    assert headers == {"X-KIP-API-Key": "api-key"}


def test_sdk_supports_proxy_bearer_identity() -> None:
    client = KipClient(bearer_token="signed-token")

    headers = client._headers()

    assert headers == {"Authorization": "Bearer signed-token"}


def test_sdk_answer_uses_public_answer_contract(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def request(
        self: KipClient,
        method: str,
        path: str,
        *,
        admin: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured.update(
            method=method,
            path=path,
            admin=admin,
            kwargs=kwargs,
        )
        return {"schema_version": "kip.answer.v1"}

    monkeypatch.setattr(KipClient, "_request", request)

    result = KipClient().answer("승인됐어?", limit=7, max_chars=9000)

    assert result["schema_version"] == "kip.answer.v1"
    assert captured == {
        "method": "POST",
        "path": "/v1/answer",
        "admin": False,
        "kwargs": {
            "json": {"query": "승인됐어?", "limit": 7, "max_chars": 9000}
        },
    }
