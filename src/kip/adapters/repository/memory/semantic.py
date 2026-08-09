from __future__ import annotations

import math
from dataclasses import dataclass

from kip.adapters.repository.memory.lexical import snippet
from kip.adapters.repository.memory.state import MemoryState
from kip.domain.json_types import JsonObject
from kip.domain.models import (
    ArtifactView,
    ContentUnit,
    EmbeddingRecord,
    EmbeddingSpace,
    RequestContext,
    SearchHit,
    SearchRequest,
)
from kip.errors import ConflictError, NotFoundError, ValidationError


@dataclass(frozen=True, slots=True)
class MemorySemanticStore:
    state: MemoryState

    def save_embedding_space(
        self,
        context: RequestContext,
        space: EmbeddingSpace,
    ) -> EmbeddingSpace:
        existing = next(
            (
                item
                for item in self.state.embedding_spaces.values()
                if item.name == space.name and item.id != space.id
            ),
            None,
        )
        if existing:
            raise ConflictError(f"embedding space name already exists: {space.name}")
        current = self.state.embedding_spaces.get(space.id)
        stored = (
            space.model_copy(update={"status": "active"})
            if current and current.status == "active"
            else space
        )
        self.state.embedding_spaces[space.id] = stored.model_copy(deep=True)
        return self.state.embedding_spaces[space.id].model_copy(deep=True)

    def active_embedding_space(
        self,
        context: RequestContext,
    ) -> EmbeddingSpace | None:
        return next(
            (
                space.model_copy(deep=True)
                for space in self.state.embedding_spaces.values()
                if space.status == "active"
            ),
            None,
        )

    def activate_embedding_space(
        self,
        context: RequestContext,
        space_id: str,
    ) -> EmbeddingSpace:
        if space_id not in self.state.embedding_spaces:
            raise NotFoundError(f"embedding space not found: {space_id}")
        for current_id, space in list(self.state.embedding_spaces.items()):
            status = (
                "active"
                if current_id == space_id
                else "shadow"
                if space.status == "active"
                else space.status
            )
            self.state.embedding_spaces[current_id] = space.model_copy(
                update={"status": status}
            )
        return self.state.embedding_spaces[space_id].model_copy(deep=True)

    def upsert_embeddings(
        self,
        context: RequestContext,
        space_id: str,
        records: list[EmbeddingRecord],
    ) -> int:
        space = self.state.embedding_spaces.get(space_id)
        if not space:
            raise NotFoundError(f"embedding space not found: {space_id}")
        for record in records:
            if len(record.embedding) != space.dimensions:
                raise ValidationError(
                    f"embedding dimension {len(record.embedding)} does not match "
                    f"{space.dimensions}"
                )
            if record.unit_id not in self.state.units:
                raise NotFoundError(f"content unit not found: {record.unit_id}")
            self.state.embeddings[(space_id, record.unit_id)] = record.model_copy(
                deep=True
            )
        return len(records)

    def vector_search(
        self,
        context: RequestContext,
        request: SearchRequest,
        query_embedding: list[float],
        *,
        space_id: str,
        limit: int,
    ) -> list[SearchHit]:
        space = self.state.embedding_spaces.get(space_id)
        if not space:
            raise NotFoundError(f"embedding space not found: {space_id}")
        if len(query_embedding) != space.dimensions:
            raise ValidationError(
                "query embedding dimension does not match embedding space"
            )
        scored: list[tuple[float, ContentUnit, ArtifactView]] = []
        for (record_space_id, unit_id), record in self.state.embeddings.items():
            if record_space_id != space_id:
                continue
            unit = self.state.units.get(unit_id)
            if not unit or (
                unit.acl_scopes
                and not set(unit.acl_scopes).issubset(context.acl_scopes)
            ):
                continue
            view = self.state.artifacts.get(unit.artifact_id)
            if not view or not view.source_object or not view.revision:
                continue
            packet = self.state.packets_by_revision.get(view.revision.id)
            if (
                not packet
                or packet.workspace_id != context.workspace
                or record.source_hash != view.revision.sha256
            ):
                continue
            if (
                request.source_kinds
                and view.source_object.system_kind not in request.source_kinds
            ):
                continue
            document_type = view.document.document_type if view.document else None
            if request.document_types and document_type not in request.document_types:
                continue
            project_id = (
                view.document.metadata.get("project_id") if view.document else None
            )
            if request.project_ids and project_id not in request.project_ids:
                continue
            scored.append(
                (
                    _cosine_similarity(query_embedding, record.embedding),
                    unit,
                    view,
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [
            _vector_hit(rank, score, unit, view, request)
            for rank, (score, unit, view) in enumerate(
                scored[:limit],
                start=1,
            )
        ]

    def semantic_status(self, context: RequestContext) -> JsonObject:
        active = self.active_embedding_space(context)
        space_vectors: JsonObject = {
            space_id: sum(
                1
                for record_space_id, _unit_id in self.state.embeddings
                if record_space_id == space_id
            )
            for space_id in self.state.embedding_spaces
        }
        space_status: JsonObject = {
            space_id: space.status
            for space_id, space in self.state.embedding_spaces.items()
        }
        return {
            "spaces": len(self.state.embedding_spaces),
            "vectors": len(self.state.embeddings),
            "active_space": active.model_dump(mode="json") if active else None,
            "space_vectors": space_vectors,
            "space_status": space_status,
        }


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValidationError(
            "query embedding dimension does not match stored vector"
        )
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def _vector_hit(
    rank: int,
    score: float,
    unit: ContentUnit,
    view: ArtifactView,
    request: SearchRequest,
) -> SearchHit:
    source_object = view.source_object
    revision = view.revision
    assert source_object is not None
    assert revision is not None
    return SearchHit(
        unit_id=unit.id,
        document_id=unit.document_id,
        artifact_id=unit.artifact_id,
        source_kind=source_object.system_kind,
        title=unit.title
        or (view.document.title if view.document else view.artifact.file_name),
        snippet=snippet(unit.body, request.query.split()),
        score=score,
        locator=unit.locator,
        source_uri=source_object.canonical_uri,
        source_sha256=revision.sha256,
        source_modified_at=revision.source_modified_at,
        metadata={
            "file_name": view.artifact.file_name,
            "document_type": view.document.document_type if view.document else None,
            "retrieval_channels": ["vector"],
            "vector_rank": rank,
        },
    )
