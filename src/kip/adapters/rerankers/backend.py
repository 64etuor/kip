from __future__ import annotations

from enum import StrEnum

from kip.errors import ConfigurationError


class RerankerBackend(StrEnum):
    HTTP = "http"
    RAPIDFUZZ = "rapidfuzz"
    BM25 = "bm25"


# 3.13.0 removed the in-process cross-encoder backends; the model runtime
# serves the same models over HTTP. Name the replacement so a config written
# for an earlier release says what to change instead of only what is wrong.
_REMOVED_BACKENDS = {
    "huggingface": 'removed in 3.13.0; use backend = "http" with the model runtime',
    "jina": 'removed in 3.13.0; use backend = "http" with the model runtime',
}


def parse_reranker_backend(value: str) -> RerankerBackend:
    try:
        return RerankerBackend(value)
    except ValueError as error:
        hint = _REMOVED_BACKENDS.get(value.strip().casefold())
        detail = f" ({hint})" if hint else ""
        raise ConfigurationError(
            f"unsupported reranker backend: {value}{detail}"
        ) from error
