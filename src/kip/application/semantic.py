from __future__ import annotations

import json
from typing import Final

from pydantic import TypeAdapter

from kip.domain.json_types import JsonObject
from kip.domain.models import (
    EmbeddableUnit,
    EmbeddingRecord,
    EmbeddingSpace,
    RequestContext,
)
from kip.errors import ConfigurationError, ConflictError, DependencyUnavailableError
from kip.ids import stable_id
from kip.ports.embedding import EmbeddingPort
from kip.ports.retrieval import RetrievalStore
from kip.settings import Settings

_STR_MAP: Final = TypeAdapter(dict[str, str])
_DOCUMENT_PROJECTION: Final = "head_tail_v1"
_TRUNCATION_MARKER: Final = "\n…\n"


def _embedding_text(unit: EmbeddableUnit, max_chars: int) -> str:
    title = unit.title.strip()
    prefix = f"{title}\n" if title else ""
    if len(prefix) >= max_chars:
        return prefix[:max_chars]
    body_budget = max_chars - len(prefix)
    body = unit.body_normalized
    if len(body) <= body_budget:
        return prefix + body
    if body_budget <= len(_TRUNCATION_MARKER):
        return prefix + body[:body_budget]
    sampled_chars = body_budget - len(_TRUNCATION_MARKER)
    head_chars = (sampled_chars + 1) // 2
    tail_chars = sampled_chars - head_chars
    tail = body[-tail_chars:] if tail_chars else ""
    return (
        prefix
        + body[:head_chars]
        + _TRUNCATION_MARKER
        + tail
    )


def _embedding_input_length(unit: EmbeddableUnit, max_chars: int) -> int:
    title_chars = len(unit.title.strip())
    separator_chars = 1 if title_chars else 0
    return min(title_chars + separator_chars + len(unit.body_normalized), max_chars)


class SemanticProjectionUseCases:
    def __init__(
        self,
        settings: Settings,
        store: RetrievalStore,
        embedding: EmbeddingPort,
    ) -> None:
        self._settings = settings
        self._store = store
        self._embedding = embedding

    def embedding_space(self, context: RequestContext) -> EmbeddingSpace:
        configured = dict(self._settings.get("models.embedding", {}) or {})
        base_space_name = str(
            configured.get("space_name")
            or f"{self._embedding.model}-{self._embedding.revision}-"
            f"{self._embedding.dimensions}"
        )
        max_document_chars = self._max_document_chars()
        space_name = (
            f"{base_space_name}-c{max_document_chars}-ht1"
        )
        configuration: dict[str, str] = {
            "space_name": space_name,
            "max_document_chars": str(max_document_chars),
            "document_projection": _DOCUMENT_PROJECTION,
        }
        if configured.get("document_instruction"):
            configuration["document_instruction"] = str(
                configured["document_instruction"]
            )
        space_id = stable_id(
            "espace",
            context.workspace,
            "\0".join(
                (
                    self._embedding.provider,
                    self._embedding.model,
                    self._embedding.revision,
                    str(self._embedding.dimensions),
                    str(self._embedding.normalized),
                    json.dumps(
                        configuration,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            ),
        )
        return EmbeddingSpace(
            id=space_id,
            name=space_name,
            provider=self._embedding.provider,
            model=self._embedding.model,
            revision=self._embedding.revision,
            dimensions=self._embedding.dimensions,
            normalized=self._embedding.normalized,
            status="shadow",
            configuration=configuration,
        )

    def rebuild(self, context: RequestContext) -> JsonObject:
        if self._embedding.name == "disabled":
            raise ConfigurationError("no embedding adapter is configured")
        space = self._store.save_embedding_space(
            context,
            self.embedding_space(context),
        )
        units = self._store.list_pending_embeddable_units(context, space.id)
        batch_size = int(self._settings.get("models.embedding.batch_size", 16))
        max_document_chars = self._max_document_chars()
        units.sort(
            key=lambda unit: (
                _embedding_input_length(unit, max_document_chars),
                unit.unit_id,
            )
        )
        newly_indexed = 0
        for offset in range(0, len(units), batch_size):
            batch = units[offset : offset + batch_size]
            texts = [
                _embedding_text(unit, max_document_chars)
                for unit in batch
            ]
            embeddings = self._embedding.embed_documents(texts)
            if len(embeddings) != len(batch):
                raise DependencyUnavailableError(
                    "embedding response count does not match semantic rebuild batch"
                )
            newly_indexed += self._store.upsert_embeddings(
                context,
                space.id,
                [
                    EmbeddingRecord(
                        unit_id=unit.unit_id,
                        embedding=embedding,
                        source_hash=unit.source_hash,
                    )
                    for unit, embedding in zip(batch, embeddings, strict=True)
                ],
            )
        progress = self._store.embedding_projection_progress(context, space.id)
        return {
            "projection": "semantic",
            "status": "shadow",
            "space_id": space.id,
            "space_name": space.name,
            "model": space.model,
            "revision": space.revision,
            "dimensions": space.dimensions,
            "indexed_units": progress.indexed_units,
            "content_units": progress.content_units,
            "newly_indexed_units": newly_indexed,
            "in_sync": progress.indexed_units == progress.content_units,
        }

    def _max_document_chars(self) -> int:
        configured = int(
            self._settings.get("models.embedding.max_document_chars", 12000)
        )
        if configured < 1:
            raise ConfigurationError(
                "embedding max_document_chars must be positive"
            )
        return configured

    def activate(
        self,
        context: RequestContext,
        space_id: str | None = None,
    ) -> EmbeddingSpace:
        selected = space_id or self.embedding_space(context).id
        verification = self.verify(context, space_id=selected)
        if verification.get("ok") is not True:
            raise ConflictError(
                "semantic projection cannot be activated until every active "
                "content unit is indexed"
            )
        return self._store.activate_embedding_space(context, selected)

    def verify(
        self,
        context: RequestContext,
        *,
        space_id: str | None = None,
    ) -> JsonObject:
        selected = space_id
        if selected is None and self._embedding.name != "disabled":
            selected = self.embedding_space(context).id
        progress = self._store.embedding_projection_progress(context, selected)
        if selected is None:
            return {
                "projection": "semantic",
                "ok": False,
                "status": "disabled",
                "space_id": None,
                "indexed_units": 0,
                "content_units": progress.content_units,
            }
        semantic = self._store.semantic_status(context)
        space_statuses = _STR_MAP.validate_python(
            semantic.get("space_status", {})
        )
        indexed = progress.indexed_units
        status = space_statuses.get(selected, "missing")
        return {
            "projection": "semantic",
            "ok": (
                status in {"shadow", "active"}
                and indexed == progress.content_units
            ),
            "status": status,
            "space_id": selected,
            "indexed_units": indexed,
            "content_units": progress.content_units,
            "in_sync": indexed == progress.content_units,
            "active": status == "active",
        }

    def search_space(
        self,
        context: RequestContext,
        *,
        explicit: bool,
    ) -> EmbeddingSpace:
        if self._embedding.name == "disabled":
            raise DependencyUnavailableError("embedding adapter is disabled")
        if explicit:
            return self.embedding_space(context)
        active = self._store.active_embedding_space(context)
        if not active:
            raise DependencyUnavailableError("no active embedding space")
        expected = self.embedding_space(context)
        if (
            active.id != expected.id
            or active.model != self._embedding.model
            or active.revision != self._embedding.revision
            or active.dimensions != self._embedding.dimensions
        ):
            raise DependencyUnavailableError(
                "active embedding space does not match the configured embedding adapter"
            )
        return active
