from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from kip.domain.generation import (
    GeneratedClaim,
    GenerationRequest,
    GenerationResult,
    GenerationUsage,
    ModelRevision,
    validate_generation_result,
)
from kip.errors import DependencyUnavailableError, ValidationError

CLAIMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "certainty": {
                        "type": "string",
                        "enum": ["supported", "uncertain"],
                    },
                },
                "required": ["text", "evidence_ids", "certainty"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

_CLAIMS_ADAPTER = TypeAdapter(tuple[GeneratedClaim, ...])


def explicit_timeout(seconds: float) -> httpx.Timeout:
    if seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    return httpx.Timeout(
        connect=seconds,
        read=seconds,
        write=seconds,
        pool=seconds,
    )


def evidence_payload(request: GenerationRequest) -> dict[str, Any]:
    return {
        "query": request.query,
        "max_claims": request.max_claims,
        "evidence": [item.model_dump(mode="json") for item in request.evidence],
    }


def read_bounded_json(
    response: httpx.Response,
    *,
    max_response_bytes: int,
) -> dict[str, Any]:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_response_bytes:
                raise DependencyUnavailableError(
                    "generation provider response exceeded configured byte limit"
                )
        except ValueError as error:
            raise DependencyUnavailableError(
                "generation provider returned an invalid content-length"
            ) from error
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > max_response_bytes:
            raise DependencyUnavailableError(
                "generation provider response exceeded configured byte limit"
            )
        chunks.append(chunk)
    try:
        parsed = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DependencyUnavailableError(
            "generation provider returned invalid JSON"
        ) from error
    if not isinstance(parsed, dict):
        raise DependencyUnavailableError(
            "generation provider returned an invalid JSON envelope"
        )
    return parsed


def parse_structured_result(
    payload: Mapping[str, Any],
    *,
    request: GenerationRequest,
    provider: str,
    model: str,
    revision: str,
    usage: GenerationUsage,
    provider_request_id: str | None,
) -> GenerationResult:
    try:
        claims = _CLAIMS_ADAPTER.validate_python(payload.get("claims"))
        result = GenerationResult(
            claims=claims,
            model=ModelRevision(provider=provider, model=model, revision=revision),
            usage=usage,
            provider_request_id=provider_request_id,
        )
    except PydanticValidationError as error:
        raise ValidationError(
            "generation response violates the structured output contract"
        ) from error
    return validate_generation_result(
        result,
        allowed_evidence_ids=tuple(item.id for item in request.evidence),
        max_claims=request.max_claims,
    )


def safe_provider_error(
    *,
    provider: str,
    status_code: int,
    request_id: str | None,
) -> DependencyUnavailableError:
    request_suffix = f" (request {request_id})" if request_id else ""
    return DependencyUnavailableError(
        f"{provider} generation provider returned status {status_code}{request_suffix}"
    )
