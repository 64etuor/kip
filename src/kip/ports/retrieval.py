from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

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


class RetrievalStore(Protocol):
    def filename_candidates(self, context: RequestContext, request: SearchRequest) -> list[str]:
        """Allowed current filesystem basenames the query spells, independent of limit/live freshness."""
        ...

    def has_visible_units(self, context: RequestContext) -> bool:
        """Whether any indexed evidence unit is visible to this caller at all."""
        ...

    def has_ambiguous_filename(self, context: RequestContext, request: SearchRequest) -> bool:
        """More than one allowed document has this exact basename, before limit."""
        ...

    def has_identifier_match(
        self, context: RequestContext, request: SearchRequest
    ) -> bool:
        """Literal, Unicode-equivalent identifier match within the request's scope."""
        ...

    def search(
        self,
        context: RequestContext,
        request: SearchRequest,
        lexemes: str,
    ) -> list[SearchHit]: ...

    def list_embeddable_units(self, context: RequestContext) -> list[EmbeddableUnit]: ...

    def list_pending_embeddable_units(
        self,
        context: RequestContext,
        space_id: str,
        *,
        after_unit_id: str | None = None,
        limit: int | None = None,
    ) -> list[EmbeddableUnit]:
        """Visible units missing or stale in ``space_id``, ordered by unit id.

        ``after_unit_id``/``limit`` page through a large corpus so a rebuild
        never loads every pending body at once.
        """
        ...

    def embedding_projection_progress(
        self,
        context: RequestContext,
        space_id: str | None,
    ) -> EmbeddingProjectionProgress: ...

    def save_embedding_space(
        self,
        context: RequestContext,
        space: EmbeddingSpace,
    ) -> EmbeddingSpace: ...

    def active_embedding_space(self, context: RequestContext) -> EmbeddingSpace | None: ...

    def embedding_space_exists(self, context: RequestContext, space_id: str) -> bool:
        """Whether the workspace has recorded `space_id`, in any status.

        A single-row lookup for hot paths such as `capabilities`; unlike
        `semantic_status` it never counts vectors.
        """
        ...

    def workspace_acl_scopes(self, context: RequestContext) -> list[str]:
        """Every ACL scope recorded by the workspace's source snapshots.

        Projection maintenance runs as the system: the vector projection must
        cover units of every scope (Slack, mail, custom source scopes), while
        search keeps filtering by the caller's own scopes.
        """
        ...

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

    def term_document_frequencies(
        self,
        context: RequestContext,
        terms: list[str],
    ) -> dict[str, int]: ...

    def any_term_visible(
        self,
        context: RequestContext,
        terms: list[str],
    ) -> bool:
        """Whether any whole term occurs in a unit this caller may see.

        The abstention gate only needs existence, so implementations stop at
        the first visible match instead of counting every document.
        """
        ...

    def get_content_units(
        self,
        context: RequestContext,
        unit_ids: Sequence[str],
    ) -> list[ContentUnit]: ...
