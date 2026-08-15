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


def test_cli_search_rejects_a_whitespace_only_query_with_a_clean_enveloped_message(
    monkeypatch,
    test_container,
    tmp_path: Path,
):
    # SearchRequest's own field validator raises this, not the CLI's
    # `provide QUERY or --query` guard (the string is non-empty).
    result = _invoke(monkeypatch, test_container, tmp_path, ["search", "   "])

    assert result.exit_code == 3
    payload = json.loads(result.stderr)
    assert payload["schema_version"] == "kip.envelope.v1"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "validation_error"
    # The raw pydantic blob (multi-line, with a docs URL) must not leak.
    assert "https://errors.pydantic.dev" not in payload["error"]["message"]
    assert "\n" not in payload["error"]["message"]


def test_missing_path_option_still_emits_the_error_envelope_not_raw_click_usage(
    monkeypatch,
    test_container,
    tmp_path: Path,
):
    # `--draft` used to declare `exists=True`, which fails during Click's
    # argument parsing -- before `_run` ever wraps the command in a
    # versioned envelope -- and printed raw Click usage text with exit
    # code 2 instead. It must now behave like any other validation error.
    result = _invoke(
        monkeypatch,
        test_container,
        tmp_path,
        ["evaluate", "draft", "validate", "--draft", "/nonexistent/draft.yaml"],
    )

    assert result.exit_code == 4, result.output
    payload = json.loads(result.stderr)
    assert payload["schema_version"] == "kip.envelope.v1"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_found"
    assert "/nonexistent/draft.yaml" in payload["error"]["message"]
    assert "--draft" in payload["error"]["message"]
    # No raw Click usage text ("Usage: ...", "Try '... --help'") leaked.
    assert "Usage:" not in result.output
    assert "Try '" not in result.output


def test_forbidden_error_message_gains_actionable_role_guidance(
    monkeypatch,
    test_container,
    tmp_path: Path,
):
    # `review approve` requires the admin role; the operator's natural
    # instinct (`--role admin` after the subcommand) fails since `--role`
    # is a root-level option. The rendered message must point at the fix
    # while keeping `error.code` exactly `forbidden`.
    result = _invoke(
        monkeypatch,
        test_container,
        tmp_path,
        ["review", "approve", "cand_does_not_matter"],
    )

    payload = json.loads(result.stderr)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "forbidden"
    assert "--role admin" in payload["error"]["message"]
    assert "BEFORE the subcommand" in payload["error"]["message"]
    assert "KIP_ROLES=admin" in payload["error"]["message"]


def test_error_code_mapping_is_shared_across_edges():
    from pydantic import ValidationError as PydanticValidationError

    from kip.domain.models import SearchRequest
    from kip.errors import (
        AuthorizationError,
        ConflictError,
        NotFoundError,
        ValidationError,
        error_code,
        http_status,
    )

    assert error_code(NotFoundError("x")) == "not_found"
    assert error_code(ConflictError("x")) == "conflict"
    assert error_code(ValidationError("x")) == "validation_error"
    assert error_code(AuthorizationError("x")) == "forbidden"
    assert error_code(RuntimeError("x")) == "internal_error"
    assert http_status(NotFoundError("x")) == 404
    assert http_status(AuthorizationError("x")) == 403

    # A request-model (Pydantic) error must map the same everywhere, not
    # internal_error on one surface and validation_error on another.
    try:
        SearchRequest(query="   ")
    except PydanticValidationError as exc:
        assert error_code(exc) == "validation_error"
        assert http_status(exc) == 422
    else:  # pragma: no cover
        raise AssertionError("expected a validation error")
