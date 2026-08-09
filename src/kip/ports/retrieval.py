from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

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


class RetrievalStore(Protocol):
    def search(
        self,
        context: RequestContext,
        request: SearchRequest,
        lexemes: str,
    ) -> list[SearchHit]: ...

    def list_embeddable_units(self, context: RequestContext) -> list[EmbeddableUnit]: ...

    def save_embedding_space(
        self,
        context: RequestContext,
        space: EmbeddingSpace,
    ) -> EmbeddingSpace: ...

    def active_embedding_space(self, context: RequestContext) -> EmbeddingSpace | None: ...

    def activate_embedding_space(
        self,
        context: RequestContext,
        space_id: str,
    ) -> EmbeddingSpace: ...

    def upsert_embeddings(
        self,
        context: RequestContext,
        space_id: str,
        records: list[EmbeddingRecord],
    ) -> int: ...

    def vector_search(
        self,
        context: RequestContext,
        request: SearchRequest,
        query_embedding: list[float],
        *,
        space_id: str,
        limit: int,
    ) -> list[SearchHit]: ...

    def semantic_status(self, context: RequestContext) -> JsonObject: ...

    def vocabulary(
        self,
        context: RequestContext,
        prefix: str,
        limit: int = 20,
    ) -> list[VocabularyItem]: ...

    def get_content_units(
        self,
        context: RequestContext,
        unit_ids: Sequence[str],
    ) -> list[ContentUnit]: ...
