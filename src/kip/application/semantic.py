from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from pydantic import TypeAdapter

from kip.domain.json_types import JsonObject
from kip.domain.models import (
    EmbeddableUnit,
    EmbeddingRecord,
    EmbeddingSpace,
    RequestContext,
    SemanticProjectionUpdate,
)
from kip.errors import ConfigurationError, ConflictError, DependencyUnavailableError
from kip.ids import stable_id
from kip.ports.embedding import EmbeddingPort
from kip.ports.retrieval import RetrievalStore
from kip.settings import Settings

_STR_MAP: Final = TypeAdapter(dict[str, str])
_DOCUMENT_PROJECTION: Final = "head_tail_v1"
_TRUNCATION_MARKER: Final = "\n…\n"


@dataclass(frozen=True, slots=True)
class ReviewedEmbeddingIdentity:
    """An embedding space identity whose retrieval quality a KIP release measured.

    Only these identities are activated automatically once complete (ADR-065);
    any other model, revision, dimension, truncation or instruction still goes
    through evaluation and an explicit ``kip projection activate``.
    """

    provider: str
    model: str
    revision: str
    dimensions: int
    max_document_chars: int
    document_projection: str
    query_instruction: str


# Shipped semantic defaults (ADR-065). Setup, the example and container
# configs and the portable gate all read these so they cannot drift.
SEMANTIC_DEFAULT_MODE: Final = "hybrid"
EMBEDDING_DEFAULTS: Final[dict[str, str | int]] = {
    "model": "kip-qwen3-embedding-0.6b",
    "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
    "dimensions": 1024,
    "batch_size": 32,
    "max_batch_chars": 16000,
    "max_document_chars": 4000,
    "timeout_seconds": 120,
    "query_timeout_seconds": 10,
    "query_instruction": "Retrieve relevant Korean evidence for this query: ",
    "space_name": "qwen3-embedding-0.6b-1024",
}

# Append new identities; never drop one a release shipped. Auto-activation
# only replaces an active space whose identity is listed here, so removing an
# earlier default would strand deployments on it as if activated by hand.
RELEASE_REVIEWED_EMBEDDING_IDENTITIES: Final = (
    ReviewedEmbeddingIdentity(
        provider="infinity",
        model="kip-qwen3-embedding-0.6b",
        revision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        dimensions=1024,
        max_document_chars=4000,
        document_projection=_DOCUMENT_PROJECTION,
        query_instruction="Retrieve relevant Korean evidence for this query: ",
    ),
)


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


def _bounded_batches(
    units: list[EmbeddableUnit],
    *,
    max_units: int,
    max_chars: int,
    max_document_chars: int,
) -> list[list[EmbeddableUnit]]:
    """Group units by count and total characters; a long unit may stand alone."""
    batches: list[list[EmbeddableUnit]] = []
    current: list[EmbeddableUnit] = []
    current_chars = 0
    for unit in units:
        length = _embedding_input_length(unit, max_document_chars)
        if current and (len(current) >= max_units or current_chars + length > max_chars):
            batches.append(current)
            current, current_chars = [], 0
        current.append(unit)
        current_chars += length
    if current:
        batches.append(current)
    return batches


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

    def rebuild(
        self,
        context: RequestContext,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> JsonObject:
        if self._embedding.name == "disabled":
            raise ConfigurationError("no embedding adapter is configured")
        space = self._store.save_embedding_space(
            context,
            self.embedding_space(context),
        )
        batch_size = int(
            self._settings.get(
                "models.embedding.batch_size",
                EMBEDDING_DEFAULTS["batch_size"],
            )
        )
        max_document_chars = self._max_document_chars()
        page_size = int(self._settings.get("models.embedding.page_size", 1000))
        if page_size < 1:
            raise ConfigurationError("embedding page_size must be positive")
        before = self._store.embedding_projection_progress(context, space.id)
        pending_total = max(before.content_units - before.indexed_units, 0)
        newly_indexed = 0
        done = 0
        after_unit_id: str | None = None
        while True:
            # Keyset pages keep memory flat on large corpora; each page is
            # sorted by length so a model batch holds similar-sized inputs.
            units = self._store.list_pending_embeddable_units(
                context,
                space.id,
                after_unit_id=after_unit_id,
                limit=page_size,
            )
            if not units:
                break
            after_unit_id = max(unit.unit_id for unit in units)
            units.sort(
                key=lambda unit: (
                    _embedding_input_length(unit, max_document_chars),
                    unit.unit_id,
                )
            )
            for batch in _bounded_batches(
                units,
                max_units=batch_size,
                max_chars=self._max_batch_chars(),
                max_document_chars=max_document_chars,
            ):
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
                done += len(batch)
                if on_progress is not None:
                    on_progress(done, max(pending_total, done))
            if len(units) < page_size:
                break
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

    def is_release_reviewed(self, space: EmbeddingSpace) -> bool:
        configured = dict(self._settings.get("models.embedding", {}) or {})
        if configured.get("document_instruction"):
            return False
        return any(
            space.provider == identity.provider
            and space.model == identity.model
            and space.revision == identity.revision
            and space.dimensions == identity.dimensions
            and str(space.configuration.get("max_document_chars"))
            == str(identity.max_document_chars)
            and space.configuration.get("document_projection")
            == identity.document_projection
            and str(
                configured.get("query_instruction", EMBEDDING_DEFAULTS["query_instruction"])
            )
            == identity.query_instruction
            for identity in RELEASE_REVIEWED_EMBEDDING_IDENTITIES
        )

    def maintain(
        self,
        context: RequestContext,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> SemanticProjectionUpdate:
        """Embed new or changed units and activate a reviewed, complete space.

        Runs after every sync and activated re-extraction so the vector channel
        follows the lexical index without a separate operator step. The model
        runtime being down is not a sync failure: search degrades to the
        lexical path and the next run resumes where this one stopped.
        """
        if not bool(self._settings.get("search.semantic_enabled", False)):
            return SemanticProjectionUpdate(status="disabled")
        if self._embedding.name == "disabled":
            return SemanticProjectionUpdate(
                status="disabled",
                reason="semantic search is enabled but models.embedding is not",
            )
        system = self.projection_context(context)
        space = self.embedding_space(system)
        try:
            rebuilt = self.rebuild(system, on_progress)
        except DependencyUnavailableError as error:
            return SemanticProjectionUpdate(
                status="unavailable",
                space_id=space.id,
                active=self._is_active(system, space.id),
                reason=(
                    f"{error}; start the model runtime (./scripts/semantic-server.sh start) "
                    "and the next sync or `kip projection rebuild --name semantic` resumes"
                ),
            )
        indexed = int(str(rebuilt.get("indexed_units", 0)))
        total = int(str(rebuilt.get("content_units", 0)))
        complete = rebuilt.get("in_sync") is True
        active = self._is_active(system, space.id)
        activated = False
        reason: str | None = None
        if complete and not active:
            activated, reason = self._auto_activate(system, space)
            active = activated
        elif not complete:
            reason = (
                f"{indexed} of {total} units are embedded; the next sync or "
                "`kip projection rebuild --name semantic` resumes"
            )
        newly = int(str(rebuilt.get("newly_indexed_units", 0)))
        status: str = "updated" if newly else "current"
        if not complete:
            status = "incomplete"
        return SemanticProjectionUpdate.model_validate(
            {
                "status": status,
                "space_id": space.id,
                "newly_indexed_units": newly,
                "indexed_units": indexed,
                "content_units": total,
                "active": active,
                "activated": activated,
                "reason": reason,
            }
        )

    def projection_context(self, context: RequestContext) -> RequestContext:
        """The caller's context widened to every ACL scope of the workspace.

        The projection is workspace data: it must hold units of every scope,
        and completeness must be judged against all of them. Search still
        filters vectors by the caller's own scopes.
        """
        scopes = sorted({*context.acl_scopes, *self._store.workspace_acl_scopes(context)})
        return context.model_copy(update={"acl_scopes": scopes})

    def activate_if_reviewed(self, context: RequestContext, space: EmbeddingSpace) -> bool:
        """Activate a complete space whose identity the release measured (ADR-065)."""
        system = self.projection_context(context)
        if self.verify(system, space_id=space.id).get("ok") is not True:
            return False
        activated, _ = self._auto_activate(system, space)
        return activated

    def _auto_activate(self, system: RequestContext, space: EmbeddingSpace) -> tuple[bool, str | None]:
        if not bool(self._settings.get("search.semantic_enabled", False)):
            return False, None
        if not bool(self._settings.get("search.semantic_auto_activate", True)):
            return False, "the space is complete; automatic activation is off, run `kip projection activate`"
        if not self.is_release_reviewed(space):
            return False, (
                "the space is complete but not active: this embedding identity was "
                "not reviewed by the KIP release, so evaluate it and run "
                "`kip projection activate`"
            )
        current = self._store.active_embedding_space(system)
        if current is not None and current.id != space.id and not self.is_release_reviewed(current):
            # Never replace a space an operator activated explicitly.
            return False, (
                f"space {current.name} was activated explicitly; run `kip projection activate` "
                "to switch to the release-reviewed space"
            )
        self._store.activate_embedding_space(system, space.id)
        return True, None

    def _is_active(self, context: RequestContext, space_id: str) -> bool:
        active = self._store.active_embedding_space(context)
        return active is not None and active.id == space_id

    def _max_batch_chars(self) -> int:
        # One request should not hold the shared model runtime for long:
        # interactive query embeddings wait behind it (ADR-065).
        configured = int(
            self._settings.get(
                "models.embedding.max_batch_chars",
                EMBEDDING_DEFAULTS["max_batch_chars"],
            )
        )
        if configured < 1:
            raise ConfigurationError("embedding max_batch_chars must be positive")
        return configured

    def _max_document_chars(self) -> int:
        # The space identity is built from this value, so a fallback that
        # disagrees with the shipped default silently builds a space no
        # release reviewed — hours of embedding that can never auto-activate.
        # Every fallback for an embedding key comes from EMBEDDING_DEFAULTS.
        configured = int(
            self._settings.get(
                "models.embedding.max_document_chars",
                EMBEDDING_DEFAULTS["max_document_chars"],
            )
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
