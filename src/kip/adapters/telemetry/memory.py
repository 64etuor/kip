from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from kip.adapters.repository.memory.state import MemoryState
from kip.domain.models import RequestContext
from kip.domain.telemetry import QueryTrace
from kip.errors import ValidationError


class MemoryQueryTraceStore:
    name: ClassVar[str] = "memory"

    def __init__(self, state: MemoryState) -> None:
        self._state = state

    def record(self, context: RequestContext, trace: QueryTrace) -> None:
        self._state.query_traces.append((context.workspace, trace))

    def list_traces(
        self,
        context: RequestContext,
        *,
        request_id: str | None = None,
        limit: int = 100,
    ) -> list[QueryTrace]:
        if not 1 <= limit <= 1000:
            raise ValidationError("query trace limit must be between 1 and 1000")
        # `PostgresQueryTraceStore.list_traces` orders by `started_at DESC,
        # id DESC`; insertion order is not the same thing once two traces
        # share a timestamp or arrive out of order.
        selected = sorted(
            (
                trace
                for workspace, trace in self._state.query_traces
                if workspace == context.workspace
                and (request_id is None or trace.request_id == request_id)
            ),
            key=lambda trace: (trace.started_at, trace.id),
            reverse=True,
        )
        return selected[:limit]

    def delete_before(self, context: RequestContext, before: datetime) -> int:
        retained = [
            item
            for item in self._state.query_traces
            if item[0] != context.workspace or item[1].started_at >= before
        ]
        deleted = len(self._state.query_traces) - len(retained)
        self._state.query_traces[:] = retained
        return deleted
