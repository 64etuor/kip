from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from kip.adapters.repository.memory.state import MemoryState
from kip.domain.json_types import JsonObject
from kip.domain.models import JobRecord, RequestContext
from kip.errors import NotFoundError
from kip.ids import new_id


@dataclass(frozen=True, slots=True)
class MemoryJobStore:
    state: MemoryState

    def enqueue_job(
        self,
        context: RequestContext,
        job_type: str,
        payload: JsonObject,
        idempotency_key: str | None = None,
    ) -> str:
        if idempotency_key:
            for job in self.state.jobs.values():
                if job.payload.get("_idempotency_key") == idempotency_key:
                    if job.status in {"succeeded", "failed"}:
                        job.status = "queued"
                        job.attempts = 0
                        self.state.job_errors.pop(job.id, None)
                    return job.id
        job_id = new_id("job")
        stored_payload = deepcopy(payload)
        if idempotency_key:
            stored_payload["_idempotency_key"] = idempotency_key
        self.state.jobs[job_id] = JobRecord(
            id=job_id,
            job_type=job_type,
            payload=stored_payload,
            status="queued",
        )
        self.state.job_order.append(job_id)
        return job_id

    def claim_job(
        self,
        context: RequestContext,
        worker_id: str,
    ) -> JobRecord | None:
        for job_id in self.state.job_order:
            job = self.state.jobs[job_id]
            if job.status != "queued":
                continue
            job.status = "running"
            job.attempts += 1
            job.payload["_worker_id"] = worker_id
            return job.model_copy(deep=True)
        return None

    def record_job_result(
        self,
        context: RequestContext,
        job_id: str,
        result: JsonObject,
    ) -> None:
        job = self.state.jobs.get(job_id)
        if not job:
            raise NotFoundError(f"job not found: {job_id}")
        job.payload["result"] = deepcopy(result)

    def complete_job(self, context: RequestContext, job_id: str) -> None:
        job = self.state.jobs.get(job_id)
        if not job:
            raise NotFoundError(f"job not found: {job_id}")
        job.status = "succeeded"

    def fail_job(self, context: RequestContext, job_id: str, error: str) -> None:
        job = self.state.jobs.get(job_id)
        if not job:
            raise NotFoundError(f"job not found: {job_id}")
        self.state.job_errors[job_id] = error
        job.status = "failed" if job.attempts >= job.max_attempts else "queued"

    def list_jobs(
        self,
        context: RequestContext,
        status: str | None = None,
        limit: int = 100,
    ) -> list[JobRecord]:
        result = [
            job.model_copy(
                update={"last_error": self.state.job_errors.get(job.id)},
                deep=True,
            )
            for job in self.state.jobs.values()
            if not status or job.status == status
        ]
        return result[:limit]
