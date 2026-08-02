from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def deduplicate_ranked_documents(document_ids: Iterable[str | None]) -> list[str]:
    return list(dict.fromkeys(document_id for document_id in document_ids if document_id))


def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0
    retrieved = set(ranked[:k])
    return len(retrieved.intersection(relevant)) / len(relevant)


def reciprocal_rank(ranked: Sequence[str], relevant: set[str]) -> float:
    for rank, document_id in enumerate(ranked, start=1):
        if document_id in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, document_id in enumerate(ranked[:k], start=1)
        if document_id in relevant
    )
    ideal_count = min(len(relevant), k)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / ideal if ideal else 0.0


def forbidden_document_count(ranked: Iterable[str], forbidden: set[str]) -> int:
    return len(set(ranked).intersection(forbidden))


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _contains(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and actual == expected
    return actual == expected


def locator_matches(actual: Mapping[str, Any], expected: Sequence[Mapping[str, Any]]) -> bool:
    return any(_contains(actual, candidate) for candidate in expected)


def percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)

