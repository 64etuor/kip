"""Keep the semantic projection in step with ingestion on every edge.

CLI, REST and the worker all run syncs; each calls these helpers so a sync or
an activated re-extraction is followed by the same incremental embedding and
reviewed auto-activation (ADR-065) instead of relying on a separate operator
step that is easy to forget.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable

from kip.application.search import RetrievalUseCases
from kip.domain.models import (
    ReextractionSummary,
    RequestContext,
    SemanticProjectionUpdate,
    SyncSummary,
)

LOGGER = logging.getLogger(__name__)


def _maintain(
    retrieval: RetrievalUseCases,
    context: RequestContext,
    progress: Callable[[int, int], None] | None,
) -> SemanticProjectionUpdate:
    # The sync has already committed; nothing in projection maintenance
    # (a database timeout, a model error) may turn it into a failure,
    # abort `sync all`, or make a worker retry the whole sync.
    try:
        return retrieval.maintain_semantic_projection(context, progress)
    except Exception as error:
        LOGGER.warning("semantic projection maintenance failed: %s", error, exc_info=True)
        return SemanticProjectionUpdate(
            status="unavailable",
            reason=(
                f"{type(error).__name__}: {error}; the sync itself succeeded, "
                "run `kip projection rebuild --name semantic` to retry"
            ),
        )


def after_sync(
    retrieval: RetrievalUseCases,
    context: RequestContext,
    summary: SyncSummary,
    progress: Callable[[int, int], None] | None = None,
) -> SyncSummary:
    update = _maintain(retrieval, context, progress)
    if update.status == "disabled" and update.reason is None:
        return summary
    warnings = list(summary.warnings)
    if update.reason:
        warnings.append(f"semantic projection: {update.reason}")
    return summary.model_copy(update={"semantic_projection": update, "warnings": warnings})


def after_reextraction(
    retrieval: RetrievalUseCases,
    context: RequestContext,
    summary: ReextractionSummary,
    progress: Callable[[int, int], None] | None = None,
) -> ReextractionSummary:
    if not summary.activated:
        return summary
    update = _maintain(retrieval, context, progress)
    if update.status == "disabled" and update.reason is None:
        return summary
    warnings = list(summary.warnings)
    if update.reason:
        warnings.append(f"semantic projection: {update.reason}")
    return summary.model_copy(update={"semantic_projection": update, "warnings": warnings})


def stderr_progress(label: str = "semantic projection", interval_seconds: float = 15.0) -> Callable[[int, int], None]:
    """Report long embedding runs on stderr so a first sync never looks hung."""
    last = [0.0]

    def report(done: int, total: int) -> None:
        now = time.monotonic()
        if done < total and now - last[0] < interval_seconds:
            return
        last[0] = now
        print(f"{label}: embedded {done}/{total} new or changed units", file=sys.stderr, flush=True)

    return report
