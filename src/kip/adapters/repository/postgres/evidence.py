from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kip.adapters.repository.postgres.database import PostgresDatabase
from kip.domain.json_types import JsonObject
from kip.domain.models import ArtifactView, ContentUnit, RequestContext


@dataclass(frozen=True, slots=True)
class PostgresEvidenceStore:
    database: PostgresDatabase

    def get_content_units(
        self,
        context: RequestContext,
        unit_ids: Sequence[str],
    ) -> list[ContentUnit]:
        return self.database.get_content_units(context, unit_ids)

    def get_content_unit(
        self,
        context: RequestContext,
        unit_id: str,
    ) -> ContentUnit:
        return self.database.get_content_unit(context, unit_id)

    def get_artifact(
        self,
        context: RequestContext,
        artifact_id: str,
    ) -> ArtifactView:
        return self.database.get_artifact(context, artifact_id)

    def get_document(
        self,
        context: RequestContext,
        document_id: str,
    ) -> JsonObject:
        return self.database.get_document(context, document_id)
