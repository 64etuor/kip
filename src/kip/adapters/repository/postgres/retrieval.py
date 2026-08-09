from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kip.adapters.repository.postgres.database import PostgresDatabase
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
class PostgresRetrievalStore:
    database: PostgresDatabase

    def search(
        self,
        context: RequestContext,
        request: SearchRequest,
        lexemes: str,
    ) -> list[SearchHit]:
        return self.database.search(context, request, lexemes)

    def list_embeddable_units(
        self,
        context: RequestContext,
    ) -> list[EmbeddableUnit]:
        return self.database.list_embeddable_units(context)

    def save_embedding_space(
        self,
        context: RequestContext,
        space: EmbeddingSpace,
    ) -> EmbeddingSpace:
        return self.database.save_embedding_space(context, space)

    def active_embedding_space(
        self,
        context: RequestContext,
    ) -> EmbeddingSpace | None:
        return self.database.active_embedding_space(context)

    def activate_embedding_space(
        self,
        context: RequestContext,
        space_id: str,
    ) -> EmbeddingSpace:
        return self.database.activate_embedding_space(context, space_id)

    def upsert_embeddings(
        self,
        context: RequestContext,
        space_id: str,
        records: list[EmbeddingRecord],
    ) -> int:
        return self.database.upsert_embeddings(context, space_id, records)

    def vector_search(
        self,
        context: RequestContext,
        request: SearchRequest,
        query_embedding: list[float],
        *,
        space_id: str,
        limit: int,
    ) -> list[SearchHit]:
        return self.database.vector_search(
            context,
            request,
            query_embedding,
            space_id=space_id,
            limit=limit,
        )

    def semantic_status(self, context: RequestContext) -> JsonObject:
        return self.database.semantic_status(context)

    def vocabulary(
        self,
        context: RequestContext,
        prefix: str,
        limit: int = 20,
    ) -> list[VocabularyItem]:
        return self.database.vocabulary(context, prefix, limit)

    def get_content_units(
        self,
        context: RequestContext,
        unit_ids: Sequence[str],
    ) -> list[ContentUnit]:
        return self.database.get_content_units(context, unit_ids)
