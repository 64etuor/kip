from __future__ import annotations

from enum import StrEnum

from kip.errors import ConfigurationError


class RerankerBackend(StrEnum):
    HTTP = "http"
    HUGGINGFACE = "huggingface"
    RAPIDFUZZ = "rapidfuzz"
    BM25 = "bm25"


def parse_reranker_backend(value: str) -> RerankerBackend:
    try:
        return RerankerBackend(value)
    except ValueError as error:
        raise ConfigurationError(f"unsupported reranker backend: {value}") from error
