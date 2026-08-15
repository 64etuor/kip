from __future__ import annotations

import logging
import time
import uuid

from pydantic import ValidationError as PydanticValidationError

from kip.container import Container, build_container
from kip.domain.models import JobRecord, RequestContext
from kip.errors import ValidationError

LOGGER = logging.getLogger(__name__)

_DEFAULT_RETRY_BACKOFF_SECONDS = 0.05
_DEFAULT_RETRY_BACKOFF_MAX_SECONDS = 1.0


def _retry_backoff_seconds(attempts: int, *, base: float, cap: float) -> float:
    """Escalating pause before the next retry attempt of a failed job.

    `attempts` is the number of attempts already made (as recorded by
    `claim_job`, which increments before returning the job), so the first
    failure (attempts == 1) waits `base` seconds, the second `base * 2`,
    and so on, capped at `cap` so a misconfigured base/cap combination can
    never stall the worker indefinitely.
    """
    escalated = base * float(2 ** max(attempts - 1, 0))
    return min(escalated, cap)


def process_job(container: Container, job: JobRecord) -> None:
    context = container.application.operations.request_context(workspace=job.payload.get("workspace") or container.settings.workspace)
    if job.job_type == "sync.source":
        source_name = str(job.payload["source_name"])
        source = container.settings.filesystem_source(source_name)
        if source:
            container.application.ingestion.sync_filesystem(context, source_name)
            return
        container.application.ingestion.sync_remote(context, source_name)
        return
    if job.job_type == "rebuild.projection":
        projection = str(job.payload["projection"])
        if projection in {"semantic", "vector"}:
            container.application.retrieval.rebuild_semantic_projection(context)
        else:
            container.application.operations.rebuild_projection(context, projection)
        return
    if job.job_type == "ontology.mine":
        raw_unit_ids = job.payload.get("unit_ids")
        if not isinstance(raw_unit_ids, list) or not all(
            isinstance(item, str) for item in raw_unit_ids
        ):
            raise ValidationError("ontology.mine requires string unit_ids")
        container.application.ontology_rag.process_mining(
            _mining_context(job),
            raw_unit_ids,
        )
        return
    raise ValidationError(f"unsupported job type: {job.job_type}")


def _mining_context(job: JobRecord) -> RequestContext:
    raw_access = job.payload.get("access")
    workspace = job.payload.get("workspace")
    if not isinstance(raw_access, dict) or not isinstance(workspace, str):
        raise ValidationError("ontology.mine requires captured access context")
    try:
        context = RequestContext.model_validate(
            {
                "workspace": workspace,
                "principal_id": raw_access.get("principal_id"),
                "acl_scopes": raw_access.get("acl_scopes"),
                "roles": raw_access.get("roles"),
                "acl_snapshot": raw_access.get("acl_snapshot"),
                "request_id": job.id,
            }
        )
    except PydanticValidationError as error:
        raise ValidationError("ontology.mine access context is invalid") from error
    if context.acl_snapshot is not None and not context.acl_snapshot.is_fresh():
        raise ValidationError("ontology.mine access snapshot expired before processing")
    return context


def run_worker(
    container: Container,
    *,
    once: bool = False,
    poll_seconds: float = 2.0,
    retry_backoff_seconds: float = _DEFAULT_RETRY_BACKOFF_SECONDS,
    retry_backoff_max_seconds: float = _DEFAULT_RETRY_BACKOFF_MAX_SECONDS,
) -> None:
    worker_id = f"worker-{uuid.uuid4().hex[:12]}"
    while True:
        context = container.application.operations.request_context()
        job = container.application.operations.claim_job(context, worker_id)
        if job is None:
            if once:
                return
            time.sleep(poll_seconds)
            continue
        try:
            process_job(container, job)
        except Exception as exc:
            LOGGER.exception("job failed", extra={"job_id": job.id})
            container.application.operations.fail_job(
                context,
                job.id,
                f"{type(exc).__name__}: {exc}",
            )
            # Only pause when the job will actually be retried (attempts
            # short of max_attempts). A persistently-broken source used to
            # get re-claimed back-to-back with no delay between attempts;
            # this bounded, escalating backoff spaces retries out instead.
            if job.attempts < job.max_attempts:
                time.sleep(
                    _retry_backoff_seconds(
                        job.attempts,
                        base=retry_backoff_seconds,
                        cap=retry_backoff_max_seconds,
                    )
                )
        else:
            container.application.operations.complete_job(context, job.id)
        if once:
            return


def main() -> None:
    run_worker(build_container())


if __name__ == "__main__":
    main()
