from __future__ import annotations

from kip.application.analyzer import KoreanNgramAnalyzer
from kip.application.search_engine import SearchEngine
from kip.application.semantic import SemanticProjectionUseCases
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
from kip.ports.embedding import EmbeddingPort
from kip.ports.evidence import EvidenceReaderPort
from kip.ports.operations import OperationsStore
from kip.ports.reranker import RerankerPort
from kip.ports.retrieval import RetrievalStore
from kip.settings import Settings


class RetrievalUseCases:
    def __init__(
        self,
        settings: Settings,
        store: RetrievalStore,
        operations: OperationsStore,
        evidence: EvidenceReaderPort,
        analyzer: KoreanNgramAnalyzer,
        embedding: EmbeddingPort,
        reranker: RerankerPort | None = None,
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
        return self._search.search(context, request, mode=mode)

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
        return ContextBundle(
            query=request.query,
            items=items,
            total_chars=total_chars,
            truncated=truncated,
        )
