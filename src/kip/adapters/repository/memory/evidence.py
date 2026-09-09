from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

from kip.adapters.repository.memory.acl import unit_is_visible
from kip.adapters.repository.memory.state import MemoryState
from kip.domain.json_types import JsonObject, JsonValue
from kip.domain.models import ArtifactView, ContentUnit, RequestContext
from kip.errors import NotFoundError


@dataclass(frozen=True, slots=True)
class MemoryEvidenceStore:
    state: MemoryState

    def get_content_units(
        self,
        context: RequestContext,
        unit_ids: Sequence[str],
    ) -> list[ContentUnit]:
        units: list[ContentUnit] = []
        for unit_id in unit_ids:
            unit = self.state.units.get(unit_id)
            if not unit or not unit_is_visible(self.state, unit, context):
                raise NotFoundError(f"content unit not found: {unit_id}")
            units.append(unit.model_copy(deep=True))
        return units

    def get_content_unit(
        self,
        context: RequestContext,
        unit_id: str,
    ) -> ContentUnit:
        return self.get_content_units(context, [unit_id])[0]

    def get_artifact(
        self,
        context: RequestContext,
        artifact_id: str,
    ) -> ArtifactView:
        view = self.state.artifacts.get(artifact_id)
        if not view:
            raise NotFoundError(f"artifact not found: {artifact_id}")
        if self.state.source_policy is not None and not self.state.source_policy.allows_artifact(view):
            raise NotFoundError(f"artifact not found: {artifact_id}")
        scopes = view.source_object.acl_scopes if view.source_object else []
        if scopes and not set(scopes).issubset(set(context.acl_scopes)):
            raise NotFoundError(f"artifact not found: {artifact_id}")
        snapshot = view.source_object.acl_snapshot if view.source_object else None
        if snapshot is not None and not snapshot.is_fresh():
            raise NotFoundError(f"artifact not found: {artifact_id}")
        return view.model_copy(deep=True)

    def get_document(
        self,
        context: RequestContext,
        document_id: str,
    ) -> JsonObject:
        document = self.state.documents.get(document_id)
        if not document:
            raise NotFoundError(f"document not found: {document_id}")
        artifacts: list[JsonValue] = []
        for packet in self.state.packets_by_revision.values():
            if packet.logical_document.id != document_id or packet.workspace_id != context.workspace:
                continue
            if self.state.current_revision_by_object.get(packet.source_object.id) != packet.revision.id:
                continue
            try:
                view = self.get_artifact(context, packet.artifact.id)
            except NotFoundError:
                continue
            artifacts.append(view.artifact.model_dump(mode="json"))
        if not artifacts:
            raise NotFoundError(f"document not found: {document_id}")
        result = deepcopy(document)
        result["artifacts"] = artifacts
        return result
