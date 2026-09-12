from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from kip.application.search_engine import SEARCH_DEFAULTS, RankedHits, SearchEngine
from kip.application.semantic import SemanticProjectionUseCases
from kip.application.telemetry import TelemetryUseCases
from kip.domain.json_types import JsonObject
from kip.domain.models import (
    ContextBundle,
    ContextItem,
    ContextRequest,
    EmbeddingSpace,
    RequestContext,
    SearchHit,
    SearchRequest,
    SemanticProjectionUpdate,
    VocabularyItem,
)
from kip.domain.telemetry import (
    QueryFilterSummary,
    QueryTrace,
    QueryTraceCandidate,
    QueryTraceModelRevision,
    TraceOutcome,
    TraceStage,
    safe_request_id,
)
from kip.errors import (
    KipError,
    error_code,
    mark_envelope_warning,
)
from kip.ports.embedding import EmbeddingPort
from kip.ports.evidence import EvidenceReaderPort
from kip.ports.knowledge import KnowledgeStore
from kip.ports.reranker import RerankerPort
from kip.ports.retrieval import RetrievalStore
from kip.ports.text_analyzer import TextAnalyzerPort
from kip.settings import Settings

_DEGRADED_FLAGS: tuple[str, ...] = (
    "semantic_degraded",
    "rerank_degraded",
    "lexical_rerank_degraded",
)
# One vocabulary for one event: `ContextBundle.truncated` is the structured
# field and `context_truncated` is its name in the query trace and in
# `meta.warnings`. Both are derived from the same bundle flag here so they
# can never disagree.
CONTEXT_TRUNCATED_WARNING = "context_truncated"
# A search that raised for a reason a retry can resolve: recorded in the trace
# and carried on the exception so every edge can name it in the error
# envelope's `meta.warnings`. It means "the search execution broke, try
# again", so it is the retryable half of a failure and `error.code` still says
# which kind (`dependency_unavailable`, `source_unavailable`, `internal_error`).
SEARCH_FAILED_WARNING = "search_failed"
# The other half. These fail identically on every retry — the request or the
# deployment is what is wrong, not a backend blip — so they keep their
# `error.code` and carry no transient marker. Without this the marker rode a
# bare `except Exception` and told an agent to retry a rejected query or a
# misconfigured deployment.
_NON_RETRYABLE_SEARCH_CODES = frozenset(
    {"validation_error", "forbidden", "not_found", "configuration_error"}
)


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """Search hits with the envelope warnings that describe how they came out."""

    hits: list[SearchHit]
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class ContextOutcome:
    """A context bundle with the envelope warnings that describe it."""

    bundle: ContextBundle
    warnings: list[str]


def _result_metadata(result: object) -> dict[str, object]:
    """Search hits carry metadata directly; context items carry it on `.hit`."""
    for candidate in (result, getattr(result, "hit", None)):
        metadata = getattr(candidate, "metadata", None)
        if isinstance(metadata, dict):
            return metadata
    return {}


class RetrievalUseCases:
    def __init__(
        self,
        settings: Settings,
        store: RetrievalStore,
        evidence: EvidenceReaderPort,
        analyzer: TextAnalyzerPort,
        embedding: EmbeddingPort,
        reranker: RerankerPort | None = None,
        telemetry: TelemetryUseCases | None = None,
        knowledge: KnowledgeStore | None = None,
        *,
        lexical_reranker: RerankerPort | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._evidence = evidence
        self._semantic = SemanticProjectionUseCases(
            settings,
            store,
            embedding,
        )
        self._search = SearchEngine(
            settings,
            store,
            analyzer,
            embedding,
            self._semantic,
            reranker,
            knowledge,
            lexical_reranker=lexical_reranker,
        )
        self._embedding = embedding
        self._reranker = reranker
        self._lexical_reranker = lexical_reranker
        self._telemetry = telemetry

    def embedding_space(self, context: RequestContext) -> EmbeddingSpace:
        return self._semantic.embedding_space(context)

    def rebuild_semantic_projection(self, context: RequestContext) -> JsonObject:
        system = self._semantic.projection_context(context)
        result = self._semantic.rebuild(system)
        space = self._semantic.embedding_space(system)
        if result.get("in_sync") is True and self._semantic.activate_if_reviewed(system, space):
            result = {**result, "status": "active", "activated": True}
        return result

    def maintain_semantic_projection(
        self,
        context: RequestContext,
        progress: Callable[[int, int], None] | None = None,
    ) -> SemanticProjectionUpdate:
        return self._semantic.maintain(context, progress)

    def activate_semantic_projection(
        self,
        context: RequestContext,
        space_id: str | None = None,
    ) -> EmbeddingSpace:
        return self._semantic.activate(context, space_id)

    def verify_semantic_projection(
        self,
        context: RequestContext,
        *,
        space_id: str | None = None,
    ) -> JsonObject:
        return self._semantic.verify(context, space_id=space_id)

    def search(
        self,
        context: RequestContext,
        request: SearchRequest,
        *,
        mode: str | None = None,
    ) -> list[SearchHit]:
        return self._ranked(context, request, mode=mode).hits

    def search_outcome(
        self,
        context: RequestContext,
        request: SearchRequest,
        *,
        mode: str | None = None,
    ) -> SearchOutcome:
        """Hits plus the `meta.warnings` every edge reports for them."""
        ranked = self._ranked(context, request, mode=mode)
        return SearchOutcome(
            hits=ranked.hits,
            warnings=self.result_warnings(
                context,
                ranked.hits,
                degraded=ranked.degraded,
            ),
        )

    def _ranked(
        self,
        context: RequestContext,
        request: SearchRequest,
        *,
        mode: str | None = None,
    ) -> RankedHits:
        started_at = datetime.now(UTC)
        started = perf_counter()
        try:
            ranked = self._search.search_ranked(
                context,
                request,
                mode=mode if mode is not None else request.mode,
            )
        except Exception as exc:
            retryable = error_code(exc) not in _NON_RETRYABLE_SEARCH_CODES
            self._record_search_trace(
                context,
                request,
                [],
                started_at=started_at,
                duration_ms=(perf_counter() - started) * 1000,
                outcome="failed",
                warnings=[SEARCH_FAILED_WARNING] if retryable else [],
            )
            if retryable:
                # The trace alone never reaches the caller; the edge adapters
                # read this back off the exception when they build the error
                # envelope.
                mark_envelope_warning(exc, SEARCH_FAILED_WARNING)
            raise
        warnings = _degradation_warnings(ranked.degraded)
        self._record_search_trace(
            context,
            request,
            ranked.hits,
            started_at=started_at,
            duration_ms=(perf_counter() - started) * 1000,
            outcome="degraded" if warnings else "succeeded",
            warnings=warnings,
        )
        return ranked

    def vocabulary(
        self,
        context: RequestContext,
        prefix: str,
        limit: int = 20,
    ) -> list[VocabularyItem]:
        return self._store.vocabulary(context, prefix, limit)

    def has_ambiguous_filename(self, context: RequestContext, request: SearchRequest) -> bool:
        return self._store.has_ambiguous_filename(context, request)

    def result_warnings(
        self,
        context: RequestContext,
        results: Sequence[object],
        *,
        degraded: Sequence[str] = (),
    ) -> list[str]:
        """Envelope warnings explaining a result without leaking scope.

        `no_visible_indexed_units` means nothing is indexed for this caller's
        workspace and access scopes; it never states that hidden units exist.
        Applies to search hits and context items alike.

        A default-mode search that fell back (model runtime down, space not
        active yet) still answers; say so instead of implying the configured
        semantic ranking was used. Degradation is reported by the search run
        (`degraded`) as well as by hit metadata, because a degraded run that
        returned nothing has no metadata left to carry it — and it must not
        be swallowed by the empty-result explanation either.
        """
        metadatas = [_result_metadata(result) for result in results]
        warnings = [
            flag
            for flag in _DEGRADED_FLAGS
            if flag in degraded or any(bool(metadata.get(flag)) for metadata in metadatas)
        ]
        if results:
            return warnings
        try:
            visible = self._store.has_visible_units(context)
        except KipError:
            # A completed empty result must not turn into an error because
            # the explanatory probe failed afterwards.
            return warnings
        return warnings if visible else [*warnings, "no_visible_indexed_units"]

    def filename_candidates(self, context: RequestContext, request: SearchRequest) -> list[str]:
        return self._store.filename_candidates(context, request)

    def context_bundle(
        self,
        context: RequestContext,
        request: ContextRequest,
    ) -> ContextBundle:
        return self._bundled(context, request)[0]

    def context_outcome(
        self,
        context: RequestContext,
        request: ContextRequest,
    ) -> ContextOutcome:
        """A bundle plus the `meta.warnings` every edge reports for it."""
        bundle, degraded = self._bundled(context, request)
        warnings = self.result_warnings(context, bundle.items, degraded=degraded)
        if bundle.truncated:
            warnings.append(CONTEXT_TRUNCATED_WARNING)
        return ContextOutcome(bundle=bundle, warnings=warnings)

    def _bundled(
        self,
        context: RequestContext,
        request: ContextRequest,
    ) -> tuple[ContextBundle, tuple[str, ...]]:
        started_at = datetime.now(UTC)
        started = perf_counter()
        ranked = self._ranked(context, request)
        hits = ranked.hits
        items: list[ContextItem] = []
        total_chars = 0
        truncated = False
        # Cap each item's contribution so one oversized unit (for example a
        # flattened XLSX sheet) cannot consume the whole bundle budget and
        # leave the remaining slots empty.
        # The configured cap is a floor for the default budget; an explicitly
        # larger max_chars raises each item's fair share so callers can
        # actually retrieve longer passages instead of silently hitting the
        # same per-item ceiling.
        item_cap = max(
            int(
                self._settings.get(
                    "search.context_item_max_chars",
                    SEARCH_DEFAULTS["context_item_max_chars"],
                )
            ),
            request.max_chars // max(1, request.limit),
        )
        for hit in hits:
            evidence = self._evidence.read_unit(
                context,
                hit.unit_id,
                verify_hash=False,
            )
            remaining = request.max_chars - total_chars
            if remaining <= 0:
                truncated = True
                break
            body = evidence.unit.body
            allowed = min(remaining, item_cap)
            if len(body) > allowed:
                body = body[:allowed]
                truncated = True
            items.append(
                ContextItem(
                    hit=hit,
                    body=body,
                    current_source_sha256=evidence.current_source_sha256,
                    source_changed_since_index=evidence.source_changed_since_index,
                    source_verification=evidence.source_verification,
                    body_truncated=len(body) < len(evidence.unit.body),
                )
            )
            total_chars += len(body)
        bundle = ContextBundle(
            query=request.query,
            items=items,
            total_chars=total_chars,
            truncated=truncated,
        )
        if self._telemetry is not None:
            self._telemetry.record(
                context,
                QueryTrace(
                    request_id=safe_request_id(context.request_id),
                    route="context",
                    outcome="degraded" if truncated else "succeeded",
                    started_at=started_at,
                    duration_ms=(perf_counter() - started) * 1000,
                    filters=_filter_summary(request),
                    stages=[
                        *_retrieval_stages(hits),
                        "exact_evidence_read",
                    ],
                    candidates=_trace_candidates(hits),
                    selected_evidence_ids=[item.hit.unit_id for item in items],
                    acl_policy_version=_acl_policy_version(context),
                    models=self._retrieval_models(hits),
                    warnings=[CONTEXT_TRUNCATED_WARNING] if truncated else [],
                ),
            )
        return bundle, ranked.degraded

    def _record_search_trace(
        self,
        context: RequestContext,
        request: SearchRequest,
        hits: list[SearchHit],
        *,
        started_at: datetime,
        duration_ms: float,
        outcome: TraceOutcome,
        warnings: list[str],
    ) -> None:
        if self._telemetry is None:
            return
        self._telemetry.record(
            context,
            QueryTrace(
                request_id=safe_request_id(context.request_id),
                route="search",
                outcome=outcome,
                started_at=started_at,
                duration_ms=duration_ms,
                filters=_filter_summary(request),
                stages=_retrieval_stages(hits),
                candidates=_trace_candidates(hits),
                acl_policy_version=_acl_policy_version(context),
                models=self._retrieval_models(hits),
                warnings=warnings,
            ),
        )

    def _retrieval_models(
        self,
        hits: list[SearchHit],
    ) -> list[QueryTraceModelRevision]:
        models: list[QueryTraceModelRevision] = []
        vector_used = any(
            "vector" in hit.metadata.get("retrieval_channels", [])
            or bool(hit.metadata.get("semantic_degraded"))
            for hit in hits
        )
        if vector_used and self._embedding.name != "disabled":
            models.append(
                QueryTraceModelRevision(
                    role="embedding",
                    provider=self._embedding.provider,
                    model=self._embedding.model,
                    revision=self._embedding.revision,
                )
            )
        # Lexical mode and the semantic fallback may use a different reranker
        # than `reranked` mode; report the one that actually scored the hits.
        reranked_by = {
            str(hit.metadata["rerank_model"])
            for hit in hits
            if "rerank_rank" in hit.metadata and "rerank_model" in hit.metadata
        }
        for reranker in (self._reranker, self._lexical_reranker):
            if reranker is None or reranker.model not in reranked_by:
                continue
            reranked_by.discard(reranker.model)
            models.append(
                QueryTraceModelRevision(
                    role="reranker",
                    provider=reranker.provider,
                    model=reranker.model,
                    revision=reranker.revision,
                )
            )
        return models


def _degradation_warnings(degraded: Sequence[str]) -> list[str]:
    """Degradation markers in their canonical envelope order."""
    return [flag for flag in _DEGRADED_FLAGS if flag in degraded]


def _filter_summary(request: SearchRequest) -> QueryFilterSummary:
    return QueryFilterSummary(
        source_kind_count=len(request.source_kinds),
        document_type_count=len(request.document_types),
        project_id_count=len(request.project_ids),
        includes_candidate_assertions=request.include_candidate_assertions,
        limit=request.limit,
    )


def _retrieval_stages(hits: list[SearchHit]) -> list[TraceStage]:
    stages: list[TraceStage] = ["acl_prefilter", "lexical"]
    channels = {
        str(channel)
        for hit in hits
        for channel in hit.metadata.get("retrieval_channels", [])
    }
    semantic_degraded = any(bool(hit.metadata.get("semantic_degraded")) for hit in hits)
    if "vector" in channels or semantic_degraded:
        stages.append("vector")
    if "lexical" in channels and "vector" in channels:
        stages.append("fusion")
    if any("rerank_rank" in hit.metadata for hit in hits):
        stages.append("rerank")
    return stages


def _trace_candidates(hits: list[SearchHit]) -> list[QueryTraceCandidate]:
    return [
        QueryTraceCandidate(
            unit_id=hit.unit_id,
            rank=rank,
            score=hit.score,
            channels=tuple(
                channel
                for channel in hit.metadata.get("retrieval_channels", [])
                if channel in {"lexical", "vector"}
            ),
        )
        for rank, hit in enumerate(hits, start=1)
    ]


def _acl_policy_version(context: RequestContext) -> str | None:
    return context.acl_snapshot.version if context.acl_snapshot is not None else None
