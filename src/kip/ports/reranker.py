from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RerankScore:
    index: int
    score: float


class RerankerPort(Protocol):
    name: str
    provider: str
    model: str
    revision: str

    def rerank(self, query: str, documents: Sequence[str]) -> list[RerankScore]:
        """Score `documents` against `query` and return every input's score.

        Ordering guarantee: implementations MUST return exactly one
        `RerankScore` per input document (by original `index`) and MUST
        order the returned list best match first, i.e. descending by
        `score` (ties broken however the adapter likes). Callers such as
        `kip.application.retrieval.apply_rerank` are entitled to rely on
        best-first ordering, but they also sort defensively, so an adapter
        that violates this guarantee degrades ranking quality rather than
        corrupting it.
        """
        ...

