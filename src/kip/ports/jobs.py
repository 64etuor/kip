from __future__ import annotations

from typing import Protocol

from kip.domain.json_types import JsonObject
from kip.domain.models import JobRecord, RequestContext


class JobStore(Protocol):
    def enqueue_job(
        self,
        context: RequestContext,
        job_type: str,
        payload: JsonObject,
        idempotency_key: str | None = None,
    ) -> str: ...

    def claim_job(self, context: RequestContext, worker_id: str) -> JobRecord | None: ...

    def complete_job(self, context: RequestContext, job_id: str) -> None: ...

    def fail_job(self, context: RequestContext, job_id: str, error: str) -> None: ...

    def list_jobs(
        self,
        context: RequestContext,
        status: str | None = None,
        limit: int = 100,
    ) -> list[JobRecord]: ...
