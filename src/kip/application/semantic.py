from __future__ import annotations

import json
from typing import Final

from pydantic import TypeAdapter

from kip.domain.json_types import JsonObject
from kip.domain.models import EmbeddingRecord, EmbeddingSpace, RequestContext
from kip.errors import ConfigurationError, ConflictError, DependencyUnavailableError
from kip.ids import stable_id
from kip.ports.embedding import EmbeddingPort
from kip.ports.operations import OperationsStore
from kip.ports.retrieval import RetrievalStore
from kip.settings import Settings

_INT_MAP: Final = TypeAdapter(dict[str, int])
_STR_MAP: Final = TypeAdapter(dict[str, str])


class SemanticProjectionUseCases:
    def __init__(
        self,
        settings: Settings,
        store: RetrievalStore,
        operations: OperationsStore,
        embedding: EmbeddingPort,
    ) -> None:
        self._settings = settings
        self._store = store
        self._operations = operations
        self._embedding = embedding

    def embedding_space(self, context: RequestContext) -> EmbeddingSpace:
        configured = dict(self._settings.get("models.embedding", {}) or {})
        space_name = str(
            configured.get("space_name")
            or f"{self._embedding.model}-{self._embedding.revision}-"
            f"{self._embedding.dimensions}"
        )
        configuration: dict[str, str] = {"space_name": space_name}
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
        units = self._store.list_embeddable_units(context)
        batch_size = int(self._settings.get("models.embedding.batch_size", 16))
        indexed = 0
        for offset in range(0, len(units), batch_size):
            batch = units[offset : offset + batch_size]
            texts = [
                "\n".join(
                    part for part in (unit.title, unit.body_normalized) if part
                )
                for unit in batch
            ]
            embeddings = self._embedding.embed_documents(texts)
            if len(embeddings) != len(batch):
                raise DependencyUnavailableError(
                    "embedding response count does not match semantic rebuild batch"
                )
            indexed += self._store.upsert_embeddings(
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
        return {
            "projection": "semantic",
            "status": "shadow",
            "space_id": space.id,
            "space_name": space.name,
            "model": space.model,
            "revision": space.revision,
            "dimensions": space.dimensions,
            "indexed_units": indexed,
            "content_units": len(units),
            "in_sync": indexed == len(units),
        }

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
        content_units = self._operations.status(context).content_units
        if self._embedding.name == "disabled" and space_id is None:
            return {
                "projection": "semantic",
                "ok": False,
                "status": "disabled",
                "space_id": None,
                "indexed_units": 0,
                "content_units": content_units,
            }
        selected = space_id or self.embedding_space(context).id
        semantic = self._store.semantic_status(context)
        vector_counts = _INT_MAP.validate_python(
            semantic.get("space_vectors", {})
        )
        space_statuses = _STR_MAP.validate_python(
            semantic.get("space_status", {})
        )
        indexed = vector_counts.get(selected, 0)
        status = space_statuses.get(selected, "missing")
        return {
            "projection": "semantic",
            "ok": status in {"shadow", "active"} and indexed == content_units,
            "status": status,
            "space_id": selected,
            "indexed_units": indexed,
            "content_units": content_units,
            "in_sync": indexed == content_units,
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
