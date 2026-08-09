from __future__ import annotations

from datetime import datetime
from typing import Protocol

from kip.domain.models import RequestContext
from kip.domain.telemetry import QueryTrace


class QueryTraceStore(Protocol):
    def record(self, context: RequestContext, trace: QueryTrace) -> None: ...

    def list_traces(
        self,
        context: RequestContext,
        *,
        request_id: str | None = None,
        limit: int = 100,
    ) -> list[QueryTrace]: ...

    def delete_before(self, context: RequestContext, before: datetime) -> int: ...


class QueryTraceExporter(Protocol):
    def export(self, trace: QueryTrace) -> None: ...
