from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from kip.adapters.repository.memory.evidence import MemoryEvidenceStore
from kip.adapters.repository.memory.lexical import MemoryLexicalStore
from kip.adapters.repository.memory.semantic import MemorySemanticStore
from kip.adapters.repository.memory.state import MemoryState
from kip.domain.embedding import EmbeddingProjectionProgress
from kip.domain.json_types import JsonObject
from kip.domain.models import (
    ContentUnit,
    EmbeddableUnit,
    EmbeddingRecord,
    EmbeddingSpace,
    RequestContext,
    SearchHit,
    SearchRequest,
    VocabularyItem,
)


@dataclass(frozen=True, slots=True)
class MemoryRetrievalStore:
    state: MemoryState
    lexical: MemoryLexicalStore = field(init=False)
    semantic: MemorySemanticStore = field(init=False)
    evidence: MemoryEvidenceStore = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "lexical", MemoryLexicalStore(self.state))
        object.__setattr__(self, "semantic", MemorySemanticStore(self.state))
        object.__setattr__(self, "evidence", MemoryEvidenceStore(self.state))

    def search(
        self,
        context: RequestContext,
        request: SearchRequest,
        lexemes: str,
    ) -> list[SearchHit]:
        return self.lexical.search(context, request, lexemes)

    def list_embeddable_units(
        self,
        context: RequestContext,
    ) -> list[EmbeddableUnit]:
        return self.lexical.list_embeddable_units(context)

    def list_pending_embeddable_units(
        self,
        context: RequestContext,
        space_id: str,
    ) -> list[EmbeddableUnit]:
        units = self.list_embeddable_units(context)
        return [
            unit
            for unit in units
            if (
                (record := self.state.embeddings.get((space_id, unit.unit_id)))
                is None
                or record.source_hash != unit.source_hash
            )
        ]

    def embedding_projection_progress(
        self,
        context: RequestContext,
        space_id: str | None,
    ) -> EmbeddingProjectionProgress:
        units = self.list_embeddable_units(context)
        indexed = sum(
            1
            for unit in units
            if (
                record := self.state.embeddings.get((space_id or "", unit.unit_id))
            )
            is not None
            and record.source_hash == unit.source_hash
        )
        return EmbeddingProjectionProgress(
            content_units=len(units),
            indexed_units=indexed,
        )

    def save_embedding_space(
        self,
        context: RequestContext,
        space: EmbeddingSpace,
    ) -> EmbeddingSpace:
        return self.semantic.save_embedding_space(context, space)

    def active_embedding_space(
        self,
        context: RequestContext,
    ) -> EmbeddingSpace | None:
        return self.semantic.active_embedding_space(context)

    def activate_embedding_space(
        self,
        context: RequestContext,
        space_id: str,
    ) -> EmbeddingSpace:
        return self.semantic.activate_embedding_space(context, space_id)

    def upsert_embeddings(
        self,
        context: RequestContext,
        space_id: str,
        records: list[EmbeddingRecord],
    ) -> int:
        return self.semantic.upsert_embeddings(context, space_id, records)

    def vector_search(
        self,
        context: RequestContext,
        request: SearchRequest,
        query_embedding: list[float],
        *,
        space_id: str,
        limit: int,
    ) -> list[SearchHit]:
        return self.semantic.vector_search(
            context,
            request,
            query_embedding,
            space_id=space_id,
            limit=limit,
        )

    def semantic_status(self, context: RequestContext) -> JsonObject:
        return self.semantic.semantic_status(context)

    def vocabulary(
        self,
        context: RequestContext,
        prefix: str,
        limit: int = 20,
    ) -> list[VocabularyItem]:
        return self.lexical.vocabulary(context, prefix, limit)

    def term_document_frequencies(
        self,
        context: RequestContext,
        terms: list[str],
    ) -> dict[str, int]:
        return self.lexical.term_document_frequencies(context, terms)

    def get_content_units(
        self,
        context: RequestContext,
        unit_ids: Sequence[str],
    ) -> list[ContentUnit]:
        return self.evidence.get_content_units(context, unit_ids)
