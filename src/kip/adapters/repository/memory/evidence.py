from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

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
        allowed_scopes = set(context.acl_scopes)
        for unit_id in unit_ids:
            unit = self.state.units.get(unit_id)
            if not unit or (
                unit.acl_scopes
                and not set(unit.acl_scopes).issubset(allowed_scopes)
            ):
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
        return view.model_copy(deep=True)

    def get_document(
        self,
        context: RequestContext,
        document_id: str,
    ) -> JsonObject:
        document = self.state.documents.get(document_id)
        if not document:
            raise NotFoundError(f"document not found: {document_id}")
        return deepcopy(document)
