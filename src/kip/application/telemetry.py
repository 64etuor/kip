from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime, timedelta

from kip.domain.models import RequestContext
from kip.domain.telemetry import QueryTrace
from kip.errors import AuthorizationError
from kip.ports.telemetry import QueryTraceExporter, QueryTraceStore


class TelemetryUseCases:
    def __init__(
        self,
        store: QueryTraceStore,
        *,
        enabled: bool,
        retention_days: int,
        exporters: tuple[QueryTraceExporter, ...] = (),
    ) -> None:
        self._store = store
        self._enabled = enabled
        self._retention_days = retention_days
        self._exporters = exporters

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record(self, context: RequestContext, trace: QueryTrace) -> None:
        if not self._enabled:
            return
        with suppress(Exception):
            self._store.record(context, trace)
        for exporter in self._exporters:
            with suppress(Exception):
                exporter.export(trace)

    def list_traces(
        self,
        context: RequestContext,
        *,
        request_id: str | None = None,
        limit: int = 100,
    ) -> list[QueryTrace]:
        if not self._enabled:
            return []
        if "admin" not in context.roles:
            raise AuthorizationError("admin role is required to inspect query traces")
        return self._store.list_traces(
            context,
            request_id=request_id,
            limit=limit,
        )

    def prune(
        self,
        context: RequestContext,
        *,
        now: datetime | None = None,
    ) -> int:
        if "admin" not in context.roles:
            raise AuthorizationError("admin role is required to prune query traces")
        selected_now = now or datetime.now(UTC)
        return self._store.delete_before(
            context,
            selected_now - timedelta(days=self._retention_days),
        )
