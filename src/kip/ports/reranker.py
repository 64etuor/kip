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

    def rerank(self, query: str, documents: Sequence[str]) -> list[RerankScore]: ...

