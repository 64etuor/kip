from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from kip.cli import app


def _invoke(monkeypatch, test_container, tmp_path: Path, args: list[str]):
    monkeypatch.setattr(
        "kip.cli.build_container",
        lambda settings, load_models=True: test_container,
    )
    return CliRunner().invoke(app, args)


def test_missing_unit_id_still_emits_the_error_envelope(
    monkeypatch,
    test_container,
    tmp_path: Path,
):
    result = _invoke(monkeypatch, test_container, tmp_path, ["read", ""])

    payload = json.loads(result.stderr)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "validation_error"
    assert payload["schema_version"] == "kip.envelope.v1"


def test_missing_artifact_id_still_emits_the_error_envelope(
    monkeypatch,
    test_container,
    tmp_path: Path,
):
    result = _invoke(
        monkeypatch,
        test_container,
        tmp_path,
        ["xlsx-read", "", "--sheet", "S", "--range", "A1:B2"],
    )

    payload = json.loads(result.stderr)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "validation_error"


def test_rest_rejects_a_blank_query_with_a_serializable_envelope(test_container):
    from fastapi.testclient import TestClient

    from kip.api import create_app

    client = TestClient(create_app(test_container))
    headers = {"X-KIP-API-Key": "test-key"}

    blank = client.post("/v1/search", headers=headers, json={"query": "   ", "limit": 3})
    good = client.post("/v1/search", headers=headers, json={"query": "정산", "limit": 3})

    assert blank.status_code == 422
    payload = blank.json()
    assert payload["error"]["code"] == "request_validation_error"
    # The envelope must be JSON-serializable even for custom validators.
    assert json.dumps(payload)
    assert good.status_code == 200
