from __future__ import annotations

import math
from collections.abc import Sequence

from rapidfuzz import __version__ as rapidfuzz_version
from rapidfuzz import fuzz, utils

from kip.errors import ConfigurationError, DependencyUnavailableError
from kip.ports.reranker import RerankScore


class RapidFuzzRerankerAdapter:
    name = "rapidfuzz"
    provider = "rapidfuzz"
    model = "wratio-token-set-v1"
    revision = rapidfuzz_version

    def __init__(
        self,
        *,
        max_document_chars: int = 8000,
        baseline_weight: float = 0.15,
    ) -> None:
        if max_document_chars < 100:
            raise ConfigurationError(
                "RapidFuzz max_document_chars must be at least 100"
            )
        if not 0.0 <= baseline_weight <= 1.0:
            raise ConfigurationError(
                "RapidFuzz baseline_weight must be between 0 and 1"
            )
        self.max_document_chars = max_document_chars
        self.baseline_weight = baseline_weight

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
    ) -> list[RerankScore]:
        if not documents:
            return []
        try:
            scores = [
                RerankScore(
                    index=index,
                    score=self._score(query, document, index),
                )
                for index, document in enumerate(documents)
            ]
        except (TypeError, ValueError) as error:
            raise DependencyUnavailableError(
                f"RapidFuzz reranking failed: {error}"
            ) from error
        if any(not math.isfinite(score.score) for score in scores):
            raise DependencyUnavailableError(
                "RapidFuzz reranking produced a non-finite score"
            )
        return sorted(scores, key=lambda score: (-score.score, score.index))

    def _score(self, query: str, document: str, index: int) -> float:
        bounded = document[: self.max_document_chars]
        similarity = max(
            fuzz.WRatio(query, bounded, processor=utils.default_process),
            fuzz.token_set_ratio(
                query,
                bounded,
                processor=utils.default_process,
            ),
        ) / 100.0
        baseline = 1.0 / (index + 1)
        return (
            (1.0 - self.baseline_weight) * similarity
            + self.baseline_weight * baseline
        )
