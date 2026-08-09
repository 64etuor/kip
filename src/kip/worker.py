from __future__ import annotations

import logging
import time
import uuid

from kip.container import Container, build_container
from kip.domain.models import JobRecord
from kip.errors import ValidationError

LOGGER = logging.getLogger(__name__)


def process_job(container: Container, job: JobRecord) -> None:
    context = container.application.operations.request_context(workspace=job.payload.get("workspace") or container.settings.workspace)
    if job.job_type == "sync.source":
        source_name = str(job.payload["source_name"])
        source = container.settings.filesystem_source(source_name)
        if source:
            container.application.ingestion.sync_filesystem(context, source_name)
            return
        if source_name == "slack":
            container.application.ingestion.sync_slack(context)
            return
        if source_name == "imap":
            container.application.ingestion.sync_imap(context)
            return
        if source_name == "apple-mail":
            container.application.ingestion.sync_apple_mail(context)
            return
        raise ValidationError(f"unknown source: {source_name}")
    if job.job_type == "rebuild.projection":
        projection = str(job.payload["projection"])
        if projection in {"semantic", "vector"}:
            container.application.retrieval.rebuild_semantic_projection(context)
        else:
            container.application.operations.rebuild_projection(context, projection)
        return
    raise ValidationError(f"unsupported job type: {job.job_type}")


def run_worker(container: Container, *, once: bool = False, poll_seconds: float = 2.0) -> None:
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
        else:
            container.application.operations.complete_job(context, job.id)
        if once:
            return


def main() -> None:
    run_worker(build_container())


if __name__ == "__main__":
    main()
