from kip.worker import run_worker


def test_worker_processes_enqueued_sync(test_container):
    path = test_container.settings.project_root / "source" / "worker.txt"
    path.write_text("worker 기반 증분 색인", encoding="utf-8")
    context = test_container.application.operations.request_context()
    job_id = test_container.application.ingestion.enqueue_sync(context, "fixture")
    run_worker(test_container, once=True, poll_seconds=0.01)
    jobs = test_container.repository.jobs.list_jobs(context)
    assert next(job for job in jobs if job.id == job_id).status == "succeeded"
    assert test_container.repository.operations.status(context).source_objects == 1


def test_recurring_idempotency_key_requeues_completed_job(test_container):
    context = test_container.application.operations.request_context()
    first = test_container.application.ingestion.enqueue_sync(context, "fixture")
    run_worker(test_container, once=True, poll_seconds=0.01)
    second = test_container.application.ingestion.enqueue_sync(context, "fixture")
    assert first == second
    queued = test_container.repository.jobs.list_jobs(context, status="queued")
    assert any(job.id == first for job in queued)
