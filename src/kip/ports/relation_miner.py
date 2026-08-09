from __future__ import annotations

from typing import Protocol

from kip.domain.knowledge import RelationMiningRequest, RelationMiningResult


class RelationMinerPort(Protocol):
    name: str
    model: str
    revision: str

    def mine(self, request: RelationMiningRequest) -> RelationMiningResult: ...
