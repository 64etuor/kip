from __future__ import annotations

from typing import Protocol

from kip.domain.models import (
    GraphEdge,
    GraphNeighborsRequest,
    GraphPath,
    GraphPathRequest,
    RequestContext,
)


class GraphProjectionPort(Protocol):
    name: str

    def neighbors(self, context: RequestContext, request: GraphNeighborsRequest) -> list[GraphEdge]: ...
    def paths(self, context: RequestContext, request: GraphPathRequest) -> list[GraphPath]: ...
    def rebuild(self, context: RequestContext) -> dict: ...
