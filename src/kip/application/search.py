from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

from kip.application.search_engine import SearchEngine
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
from kip.ports.embedding import EmbeddingPort
from kip.ports.evidence import EvidenceReaderPort
from kip.ports.operations import OperationsStore
from kip.ports.reranker import RerankerPort
from kip.ports.retrieval import RetrievalStore
from kip.ports.text_analyzer import TextAnalyzerPort
from kip.settings import Settings


class RetrievalUseCases:
    def __init__(
        self,
        settings: Settings,
        store: RetrievalStore,
        operations: OperationsStore,
        evidence: EvidenceReaderPort,
        analyzer: TextAnalyzerPort,
        embedding: EmbeddingPort,
        reranker: RerankerPort | None = None,
        telemetry: TelemetryUseCases | None = None,
    ) -> None:
        self._store = store
        self._evidence = evidence
        self._semantic = SemanticProjectionUseCases(
            settings,
            store,
            operations,
            embedding,
        )
        self._search = SearchEngine(
            settings,
            store,
            analyzer,
            embedding,
            self._semantic,
            reranker,
        )
        self._embedding = embedding
        self._reranker = reranker
        self._telemetry = telemetry

    def embedding_space(self, context: RequestContext) -> EmbeddingSpace:
        return self._semantic.embedding_space(context)

    def rebuild_semantic_projection(self, context: RequestContext) -> JsonObject:
        return self._semantic.rebuild(context)

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
        started_at = datetime.now(UTC)
        started = perf_counter()
        try:
            hits = self._search.search(context, request, mode=mode)
        except Exception:
            self._record_search_trace(
                context,
                request,
                [],
                started_at=started_at,
                duration_ms=(perf_counter() - started) * 1000,
                outcome="failed",
                warnings=["search_failed"],
            )
            raise
        degraded = any(bool(hit.metadata.get("semantic_degraded")) for hit in hits)
        self._record_search_trace(
            context,
            request,
            hits,
            started_at=started_at,
            duration_ms=(perf_counter() - started) * 1000,
            outcome="degraded" if degraded else "succeeded",
            warnings=["semantic_degraded"] if degraded else [],
        )
        return hits

    def vocabulary(
        self,
        context: RequestContext,
        prefix: str,
        limit: int = 20,
    ) -> list[VocabularyItem]:
        return self._store.vocabulary(context, prefix, limit)

    def context_bundle(
        self,
        context: RequestContext,
        request: ContextRequest,
    ) -> ContextBundle:
        started_at = datetime.now(UTC)
        started = perf_counter()
        hits = self.search(context, request)
        items: list[ContextItem] = []
        total_chars = 0
        truncated = False
        for hit in hits:
            evidence = self._evidence.read_unit(context, hit.unit_id)
            remaining = request.max_chars - total_chars
            if remaining <= 0:
                truncated = True
                break
            body = evidence.unit.body
            if len(body) > remaining:
                body = body[:remaining]
                truncated = True
            items.append(
                ContextItem(
                    hit=hit,
                    body=body,
                    current_source_sha256=evidence.current_source_sha256,
                    source_changed_since_index=evidence.source_changed_since_index,
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
                    warnings=["context_truncated"] if truncated else [],
                ),
            )
        return bundle

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
        if self._reranker is not None and any(
            "rerank_rank" in hit.metadata for hit in hits
        ):
            models.append(
                QueryTraceModelRevision(
                    role="reranker",
                    provider=self._reranker.provider,
                    model=self._reranker.model,
                    revision=self._reranker.revision,
                )
            )
        return models


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
