from __future__ import annotations

from enum import StrEnum

from kip.errors import ConfigurationError


class GenerationProviderKind(StrEnum):
    LOCAL = "local"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


def parse_generation_provider_kind(value: str) -> GenerationProviderKind:
    try:
        return GenerationProviderKind(value)
    except ValueError as error:
        raise ConfigurationError(
            f"unsupported generation provider: {value or 'missing'}"
        ) from error
