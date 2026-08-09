from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from kip.adapters.repository.postgres.database import PostgresDatabase, _json
from kip.domain.models import RequestContext
from kip.domain.telemetry import QueryTrace
from kip.errors import ValidationError


class PostgresQueryTraceStore:
    name: ClassVar[str] = "postgresql"

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def record(self, context: RequestContext, trace: QueryTrace) -> None:
        with self._database._connection(context) as connection:
            self._database._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO audit.query_traces(
                        id, workspace_id, request_id, route, outcome,
                        started_at, duration_ms, payload
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    """,
                    (
                        trace.id,
                        context.workspace,
                        trace.request_id,
                        trace.route,
                        trace.outcome,
                        trace.started_at,
                        trace.duration_ms,
                        _json(trace.model_dump(mode="json")),
                    ),
                )
            connection.commit()

    def list_traces(
        self,
        context: RequestContext,
        *,
        request_id: str | None = None,
        limit: int = 100,
    ) -> list[QueryTrace]:
        if not 1 <= limit <= 1000:
            raise ValidationError("query trace limit must be between 1 and 1000")
        with (
            self._database._connection(context) as connection,
            connection.cursor() as cursor,
        ):
            if request_id is None:
                cursor.execute(
                    """
                    SELECT payload
                    FROM audit.query_traces
                    WHERE workspace_id=%s
                    ORDER BY started_at DESC, id DESC
                    LIMIT %s
                    """,
                    (context.workspace, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT payload
                    FROM audit.query_traces
                    WHERE workspace_id=%s AND request_id=%s
                    ORDER BY started_at DESC, id DESC
                    LIMIT %s
                    """,
                    (context.workspace, request_id, limit),
                )
            return [QueryTrace.model_validate(row["payload"]) for row in cursor.fetchall()]

    def delete_before(self, context: RequestContext, before: datetime) -> int:
        with self._database._connection(context) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM audit.query_traces
                    WHERE workspace_id=%s AND started_at < %s
                    """,
                    (context.workspace, before),
                )
                deleted = cursor.rowcount
            connection.commit()
        return deleted
