from __future__ import annotations

import json

import httpx
import pytest

from kip.adapters.generators.anthropic import AnthropicGenerationAdapter
from kip.adapters.generators.openai_compatible import OpenAICompatibleGenerationAdapter
from kip.container import build_container
from kip.domain.generation import GenerationEvidence, GenerationRequest
from kip.errors import ConfigurationError, DependencyUnavailableError, ValidationError
from kip.settings import Settings


def _request() -> GenerationRequest:
    return GenerationRequest(
        query="A과제 변경이 승인됐어?",
        evidence=(
            GenerationEvidence(
                id="unit_1",
                body="A과제 참여율 변경이 2026-08-01 승인되었다.",
                locator="page:3",
            ),
        ),
        max_claims=3,
        max_output_tokens=512,
    )


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_openai_adapter_uses_responses_structured_output_wire_contract() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        payload = json.loads(request.content)
        assert request.url == "https://api.openai.test/v1/responses"
        assert request.headers["authorization"] == "Bearer openai-secret"
        assert payload["model"] == "answer-model"
        assert payload["store"] is False
        assert payload["max_output_tokens"] == 512
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["text"]["format"]["strict"] is True
        schema = payload["text"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert schema["properties"]["claims"]["items"]["additionalProperties"] is False
        return httpx.Response(
            200,
            headers={"x-request-id": "req_openai"},
            json={
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "claims": [
                                            {
                                                "text": "변경이 승인되었다.",
                                                "evidence_ids": ["unit_1"],
                                                "certainty": "supported",
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 31, "output_tokens": 12, "total_tokens": 43},
            },
        )

    adapter = OpenAICompatibleGenerationAdapter(
        base_url="https://api.openai.test",
        api_key="openai-secret",
        model="answer-model",
        revision="2026-08-01",
        allow_remote_egress=True,
        client=_client(httpx.MockTransport(handler)),
    )

    result = adapter.generate(_request())

    assert result.claims[0].evidence_ids == ("unit_1",)
    assert result.usage.total_tokens == 43
    assert result.provider_request_id == "req_openai"
    assert len(captured) == 1


def test_anthropic_adapter_uses_messages_structured_output_wire_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url == "https://api.anthropic.test/v1/messages"
        assert request.headers["x-api-key"] == "anthropic-secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert payload["model"] == "answer-model"
        assert payload["max_tokens"] == 512
        assert payload["output_config"]["format"]["type"] == "json_schema"
        schema = payload["output_config"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        return httpx.Response(
            200,
            headers={"request-id": "req_anthropic"},
            json={
                "id": "msg_1",
                "type": "message",
                "stop_reason": "end_turn",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "claims": [
                                    {
                                        "text": "변경이 승인되었다.",
                                        "evidence_ids": ["unit_1"],
                                        "certainty": "supported",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
                "usage": {"input_tokens": 28, "output_tokens": 11},
            },
        )

    adapter = AnthropicGenerationAdapter(
        base_url="https://api.anthropic.test",
        api_key="anthropic-secret",
        model="answer-model",
        revision="2026-08-01",
        allow_remote_egress=True,
        client=_client(httpx.MockTransport(handler)),
    )

    result = adapter.generate(_request())

    assert result.claims[0].text == "변경이 승인되었다."
    assert result.usage.total_tokens == 39
    assert result.provider_request_id == "req_anthropic"


@pytest.mark.parametrize("adapter_kind", ["openai", "anthropic"])
def test_generator_rejects_unknown_evidence_from_provider(adapter_kind: str) -> None:
    claim = {
        "claims": [
            {
                "text": "근거 없는 주장",
                "evidence_ids": ["unit_unknown"],
                "certainty": "supported",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if adapter_kind == "openai":
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(claim)}]}
                    ],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
            )
        return httpx.Response(
            200,
            json={
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": json.dumps(claim)}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    common = {
        "base_url": f"https://api.{adapter_kind}.test",
        "api_key": "secret-value",
        "model": "answer-model",
        "revision": "2026-08-01",
        "allow_remote_egress": True,
        "client": _client(httpx.MockTransport(handler)),
    }
    adapter = (
        OpenAICompatibleGenerationAdapter(**common)
        if adapter_kind == "openai"
        else AnthropicGenerationAdapter(**common)
    )

    with pytest.raises(ValidationError, match="unknown evidence IDs"):
        adapter.generate(_request())


def test_generator_wraps_timeout_without_leaking_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret-value timed out", request=request)

    adapter = OpenAICompatibleGenerationAdapter(
        base_url="https://api.openai.test",
        api_key="secret-value",
        model="answer-model",
        revision="2026-08-01",
        allow_remote_egress=True,
        client=_client(httpx.MockTransport(handler)),
    )

    with pytest.raises(DependencyUnavailableError) as caught:
        adapter.generate(_request())
    assert "secret-value" not in str(caught.value)
    assert "timeout" in str(caught.value)


def test_generator_rejects_non_json_and_oversized_responses() -> None:
    non_json = OpenAICompatibleGenerationAdapter(
        base_url="http://127.0.0.1:7998",
        api_key="",
        model="answer-model",
        revision="local-v1",
        max_response_bytes=128,
        client=_client(httpx.MockTransport(lambda request: httpx.Response(200, text="not-json"))),
    )
    oversized = OpenAICompatibleGenerationAdapter(
        base_url="http://127.0.0.1:7998",
        api_key="",
        model="answer-model",
        revision="local-v1",
        max_response_bytes=8,
        client=_client(httpx.MockTransport(lambda request: httpx.Response(200, content=b"0123456789"))),
    )

    with pytest.raises(DependencyUnavailableError, match="invalid JSON"):
        non_json.generate(_request())
    with pytest.raises(DependencyUnavailableError, match="response exceeded"):
        oversized.generate(_request())


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (429, {"error": {"message": "secret-value quota"}}, "status 429"),
        (200, {"status": "cancelled", "output": [], "usage": {}}, "cancelled"),
    ],
)
def test_openai_provider_errors_and_cancellation_are_redacted(
    status: int,
    payload: dict[str, object],
    expected: str,
) -> None:
    adapter = OpenAICompatibleGenerationAdapter(
        base_url="https://api.openai.test",
        api_key="secret-value",
        model="answer-model",
        revision="2026-08-01",
        allow_remote_egress=True,
        client=_client(
            httpx.MockTransport(
                lambda request: httpx.Response(
                    status,
                    headers={"x-request-id": "req_failed"},
                    json=payload,
                )
            )
        ),
    )

    with pytest.raises(DependencyUnavailableError) as caught:
        adapter.generate(_request())
    message = str(caught.value)
    assert expected in message
    assert "req_failed" in message
    assert "secret-value" not in message


def test_container_builds_local_generator_from_configuration(tmp_path) -> None:
    settings = Settings.for_test()
    settings.cas_path = tmp_path / "cas"
    settings.raw["models"] = {
        "generation": {
            "enabled": True,
            "provider": "local",
            "base_url": "http://127.0.0.1:7998",
            "model": "local-answer-model",
            "revision": "sha256:abc123",
        }
    }

    container = build_container(settings=settings)

    assert isinstance(container.generator, OpenAICompatibleGenerationAdapter)
    assert container.generator.provider == "local"
    assert container.generator.model == "local-answer-model"


def test_container_requires_configured_remote_secret_reference(tmp_path) -> None:
    settings = Settings.for_test()
    settings.cas_path = tmp_path / "cas"
    settings.raw["security"] = {"allow_remote_model_egress": True}
    settings.raw["models"] = {
        "generation": {
            "enabled": True,
            "provider": "openai",
            "base_url": "https://api.openai.test",
            "model": "answer-model",
            "revision": "2026-08-01",
            "secret_ref": "env:KIP_TEST_MISSING_GENERATION_KEY",
            "allowed_classifications": ["public"],
            "retention_policy": "zero_retention",
        }
    }

    with pytest.raises(ConfigurationError, match="KIP_TEST_MISSING_GENERATION_KEY"):
        build_container(settings=settings)
