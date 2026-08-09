from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

from kip.adapters.repository.memory.acl import unit_is_visible
from kip.adapters.repository.memory.state import MemoryState
from kip.domain.json_types import JsonObject
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
        packet = next(
            (
                item
                for item in self.state.packets_by_revision.values()
                if item.logical_document.id == document_id
            ),
            None,
        )
        if packet is None or any(
            not unit_is_visible(self.state, unit, context) for unit in packet.units
        ):
            raise NotFoundError(f"document not found: {document_id}")
        return deepcopy(document)
