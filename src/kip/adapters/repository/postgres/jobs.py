from __future__ import annotations

from dataclasses import dataclass

from kip.adapters.repository.postgres.database import PostgresDatabase
from kip.domain.json_types import JsonObject
from kip.domain.models import JobRecord, RequestContext


@dataclass(frozen=True, slots=True)
class PostgresJobStore:
    database: PostgresDatabase

    def enqueue_job(
        self,
        context: RequestContext,
        job_type: str,
        payload: JsonObject,
        idempotency_key: str | None = None,
    ) -> str:
        return self.database.enqueue_job(
            context,
            job_type,
            payload,
            idempotency_key,
        )

    def claim_job(
        self,
        context: RequestContext,
        worker_id: str,
    ) -> JobRecord | None:
        return self.database.claim_job(context, worker_id)

    def complete_job(self, context: RequestContext, job_id: str) -> None:
        self.database.complete_job(context, job_id)

    def fail_job(self, context: RequestContext, job_id: str, error: str) -> None:
        self.database.fail_job(context, job_id, error)

    def list_jobs(
        self,
        context: RequestContext,
        status: str | None = None,
        limit: int = 100,
    ) -> list[JobRecord]:
        return self.database.list_jobs(context, status, limit)
