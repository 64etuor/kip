from __future__ import annotations

from dataclasses import dataclass

from kip.adapters.repository.memory.state import MemoryState
from kip.domain.models import (
    ArtifactView,
    DocumentPacket,
    IngestResult,
    RequestContext,
)
from kip.errors import ValidationError


@dataclass(frozen=True, slots=True)
class MemoryIngestionStore:
    state: MemoryState

    def has_revision(
        self,
        context: RequestContext,
        source_object_id: str,
        sha256: str,
    ) -> bool:
        revision_id = self.state.current_revision_by_object.get(source_object_id)
        if not revision_id:
            return False
        packet = self.state.packets_by_revision.get(revision_id)
        return bool(
            packet
            and packet.revision.sha256 == sha256
            and packet.workspace_id == context.workspace
        )

    def ingest_packet(
        self,
        context: RequestContext,
        packet: DocumentPacket,
    ) -> IngestResult:
        if packet.workspace_id != context.workspace:
            raise ValidationError("packet workspace does not match request context")
        old_revision_id = self.state.current_revision_by_object.get(
            packet.source_object.id
        )
        old_packet = self.state.packets_by_revision.get(old_revision_id or "")
        if old_packet and old_packet.revision.sha256 == packet.revision.sha256:
            return IngestResult(
                status="unchanged",
                source_object_id=packet.source_object.id,
                revision_id=old_packet.revision.id,
                artifact_id=old_packet.artifact.id,
                document_id=old_packet.logical_document.id,
                extraction_id=old_packet.extraction.id,
                unit_count=len(old_packet.units),
                warnings=list(old_packet.extraction.warnings),
            )
        if old_packet:
            for unit in old_packet.units:
                self.state.units.pop(unit.id, None)

        stored = packet.model_copy(deep=True)
        self.state.packets_by_revision[stored.revision.id] = stored
        self.state.current_revision_by_object[
            stored.source_object.id
        ] = stored.revision.id
        self.state.documents[stored.logical_document.id] = {
            "document": stored.logical_document.model_dump(mode="json"),
            "artifacts": [stored.artifact.model_dump(mode="json")],
            "current_revision_id": stored.revision.id,
        }
        self.state.artifacts[stored.artifact.id] = ArtifactView(
            artifact=stored.artifact,
            document=stored.logical_document,
            source_object=stored.source_object,
            revision=stored.revision,
        )
        for unit in stored.units:
            self.state.units[unit.id] = unit

        return IngestResult(
            status="replaced" if old_packet else "inserted",
            source_object_id=stored.source_object.id,
            revision_id=stored.revision.id,
            artifact_id=stored.artifact.id,
            document_id=stored.logical_document.id,
            extraction_id=stored.extraction.id,
            unit_count=len(stored.units),
            warnings=list(stored.extraction.warnings),
        )
