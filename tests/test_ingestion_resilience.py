from __future__ import annotations

import time

from kip.domain.models import SearchRequest
from kip.worker import run_worker


def test_bare_exception_from_one_file_does_not_abort_the_whole_sync(
    test_container, monkeypatch
):
    """A parser bug that raises something other than KipError/OSError (e.g.
    a third-party pymupdf or XML parser exception escaping unnoticed) must
    not crash the whole `sync_filesystem` run: it should be recorded as a
    per-file failure and the rest of the corpus must still be indexed.
    """
    source = test_container.settings.project_root / "source"
    (source / "good_one.txt").write_text("정산 기준 정상 문서 A", encoding="utf-8")
    (source / "good_two.txt").write_text("정산 기준 정상 문서 B", encoding="utf-8")
    (source / "bad.txt").write_text("정산 기준 손상된 문서", encoding="utf-8")
    context = test_container.application.operations.request_context()

    ingestion = test_container.application.ingestion
    original_ingest_file = ingestion.ingest_file

    def _flaky_ingest_file(ctx, *, record, **kwargs):
        if record.relative_path == "bad.txt":
            # Simulate a non-KipError/OSError exception escaping a parser,
            # e.g. pymupdf's FzErrorFormat or xml.etree.ElementTree.ParseError.
            raise RuntimeError("simulated unparseable file")
        return original_ingest_file(ctx, record=record, **kwargs)

    monkeypatch.setattr(ingestion, "ingest_file", _flaky_ingest_file)

    summary = ingestion.sync_filesystem(context, "fixture")

    assert summary.scanned == 3
    assert summary.inserted == 2
    assert summary.failed == 1
    assert any(
        "bad.txt" in warning and "RuntimeError" in warning
        for warning in summary.warnings
    )

    hits = test_container.application.retrieval.search(
        context, SearchRequest(query="정상 문서", limit=10)
    )
    assert len(hits) == 2


def test_worker_retries_a_persistently_failing_job_with_escalating_bounded_backoff(
    test_container, monkeypatch
):
    context = test_container.application.operations.request_context()
    job_id = test_container.application.operations.enqueue_job(
        context, "unsupported.job.type", {}
    )

    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))

    for _ in range(5):
        run_worker(
            test_container,
            once=True,
            poll_seconds=0.001,
            retry_backoff_seconds=0.01,
            retry_backoff_max_seconds=0.05,
        )

    jobs = test_container.application.operations.list_jobs(context)
    job = next(job for job in jobs if job.id == job_id)

    assert job.attempts == job.max_attempts == 5
    assert job.status == "failed"
    # Each of the first 4 (non-terminal) attempts pauses with an escalating,
    # capped backoff; the 5th (terminal) attempt gives up immediately
    # instead of sleeping for a retry that will never happen.
    assert sleeps == [0.01, 0.02, 0.04, 0.05]


def test_worker_backoff_defaults_are_short_and_bounded():
    from kip.worker import (
        _DEFAULT_RETRY_BACKOFF_MAX_SECONDS,
        _DEFAULT_RETRY_BACKOFF_SECONDS,
        _retry_backoff_seconds,
    )

    delays = [
        _retry_backoff_seconds(
            attempt,
            base=_DEFAULT_RETRY_BACKOFF_SECONDS,
            cap=_DEFAULT_RETRY_BACKOFF_MAX_SECONDS,
        )
        for attempt in range(1, 5)
    ]

    assert delays == sorted(delays)
    assert delays[0] > 0
    assert all(delay <= _DEFAULT_RETRY_BACKOFF_MAX_SECONDS for delay in delays)
