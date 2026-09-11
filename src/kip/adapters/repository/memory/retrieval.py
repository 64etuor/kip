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

    def has_identifier_match(self, context: RequestContext, request: SearchRequest) -> bool:
        return self.lexical.has_identifier_match(context, request)

    def has_ambiguous_filename(self, context: RequestContext, request: SearchRequest) -> bool:
        return self.lexical.has_ambiguous_filename(context, request)

    def filename_candidates(self, context: RequestContext, request: SearchRequest) -> list[str]:
        return self.lexical.filename_candidates(context, request)

    def has_visible_units(self, context: RequestContext) -> bool:
        return self.lexical.has_visible_units(context)

    def list_embeddable_units(
        self,
        context: RequestContext,
    ) -> list[EmbeddableUnit]:
        return self.lexical.list_embeddable_units(context)

    def workspace_acl_scopes(self, context: RequestContext) -> list[str]:
        scopes: set[str] = set()
        for packet in self.state.packets_by_revision.values():
            if packet.workspace_id != context.workspace:
                continue
            snapshot = packet.source_object.acl_snapshot
            if snapshot is not None:
                scopes.update(snapshot.scopes)
            scopes.update(packet.source_object.acl_scopes)
            for unit in packet.units:
                scopes.update(unit.acl_scopes)
        return sorted(scopes)

    def list_pending_embeddable_units(
        self,
        context: RequestContext,
        space_id: str,
        *,
        after_unit_id: str | None = None,
        limit: int | None = None,
    ) -> list[EmbeddableUnit]:
        units = sorted(self.list_embeddable_units(context), key=lambda unit: unit.unit_id)
        pending = [
            unit
            for unit in units
            if (after_unit_id is None or unit.unit_id > after_unit_id)
            and (
                (record := self.state.embeddings.get((space_id, unit.unit_id)))
                is None
                or record.source_hash != unit.source_hash
            )
        ]
        return pending if limit is None else pending[:limit]

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

    def embedding_space_exists(self, context: RequestContext, space_id: str) -> bool:
        return self.semantic.embedding_space_exists(context, space_id)

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

    def any_term_visible(
        self,
        context: RequestContext,
        terms: list[str],
    ) -> bool:
        return self.lexical.any_term_visible(context, terms)

    def get_content_units(
        self,
        context: RequestContext,
        unit_ids: Sequence[str],
    ) -> list[ContentUnit]:
        return self.evidence.get_content_units(context, unit_ids)
