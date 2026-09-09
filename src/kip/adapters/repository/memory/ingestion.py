from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from kip.adapters.repository.memory.state import MemoryState
from kip.domain.egress import DataClassification
from kip.domain.identity import AclSnapshot
from kip.domain.models import (
    ArtifactView,
    DocumentPacket,
    IngestResult,
    RequestContext,
    SourceObjectAbsence,
    SourceRevision,
)
from kip.errors import ConflictError, ValidationError


@dataclass(frozen=True, slots=True)
class MemoryIngestionStore:
    state: MemoryState

    def current_source_revision(
        self, context: RequestContext, source_object_id: str
    ) -> SourceRevision | None:
        revision_id = self.state.current_revision_by_object.get(source_object_id)
        packet = self.state.packets_by_revision.get(revision_id or "")
        if packet is None or packet.workspace_id != context.workspace:
            return None
        if not set(packet.source_object.acl_scopes).issubset(context.acl_scopes):
            return None
        return packet.revision.model_copy(deep=True)

    def upsert_acl_snapshot(
        self,
        context: RequestContext,
        source_object_id: str,
        snapshot: AclSnapshot,
        classification: DataClassification,
    ) -> None:
        self.state.acl_snapshots[snapshot.id] = snapshot.model_copy(deep=True)
        revision_id = self.state.current_revision_by_object.get(source_object_id)
        packet = self.state.packets_by_revision.get(revision_id or "")
        if packet is None or packet.workspace_id != context.workspace:
            return
        packet.source_object.acl_snapshot = snapshot.model_copy(deep=True)
        packet.source_object.acl_scopes = list(snapshot.scopes)
        packet.source_object.classification = classification
        for unit in packet.units:
            unit.acl_snapshot_id = snapshot.id
            unit.acl_scopes = list(snapshot.scopes)
            unit.classification = classification
            self.state.units[unit.id] = unit
        view = self.state.artifacts.get(packet.artifact.id)
        if view is not None:
            view.source_object = packet.source_object.model_copy(deep=True)
        self._refresh_assertion_access({unit.id for unit in packet.units})

    def _refresh_assertion_access(self, changed_unit_ids: set[str]) -> None:
        for assertion in self.state.assertions.values():
            if not changed_unit_ids.intersection(assertion.evidence_unit_ids):
                continue
            units = [self.state.units.get(unit_id) for unit_id in assertion.evidence_unit_ids]
            if any(unit is None for unit in units):
                # Never derive a broader policy from incomplete evidence.
                continue
            assertion.acl_scopes = sorted({scope for unit in units if unit is not None for scope in unit.acl_scopes}) or assertion.acl_scopes
            assertion.evidence_acl_snapshot_ids = sorted({unit.acl_snapshot_id for unit in units if unit is not None and unit.acl_snapshot_id is not None})

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

    def current_revision_by_stat(
        self,
        context: RequestContext,
        source_object_id: str,
        *,
        size: int,
        mtime_ns: int,
    ) -> str | None:
        revision_id = self.state.current_revision_by_object.get(source_object_id)
        if not revision_id:
            return None
        packet = self.state.packets_by_revision.get(revision_id)
        if (
            packet is None
            or packet.workspace_id != context.workspace
            or packet.revision.is_tombstone
            or packet.revision.size_bytes != size
            or packet.revision.metadata.get("mtime_ns") != mtime_ns
        ):
            return None
        return packet.revision.id

    def reconcile_scan_absences(
        self,
        context: RequestContext,
        system_id: str,
        seen_object_ids: AbstractSet[str],
    ) -> list[SourceObjectAbsence]:
        absences: list[SourceObjectAbsence] = []
        for object_id, revision_id in self.state.current_revision_by_object.items():
            packet = self.state.packets_by_revision.get(revision_id)
            if (
                packet is None
                or packet.workspace_id != context.workspace
                or packet.source_object.system_id != system_id
            ):
                continue
            if object_id in seen_object_ids:
                # A reappearing object clears its absence mark.
                self.state.absent_scan_counts.pop(object_id, None)
                continue
            if packet.revision.is_tombstone:
                continue
            count = self.state.absent_scan_counts.get(object_id, 0) + 1
            self.state.absent_scan_counts[object_id] = count
            absences.append(
                SourceObjectAbsence(
                    object_id=object_id,
                    external_id=packet.source_object.external_id,
                    artifact_id=packet.artifact.id,
                    absent_scan_count=count,
                )
            )
        return absences

    def ingest_packet(
        self,
        context: RequestContext,
        packet: DocumentPacket,
    ) -> IngestResult:
        if packet.workspace_id != context.workspace:
            raise ValidationError("packet workspace does not match request context")
        snapshot = packet.source_object.acl_snapshot
        if snapshot is not None:
            mismatched = [
                unit.id
                for unit in packet.units
                if unit.acl_snapshot_id != snapshot.id
            ]
            if mismatched:
                raise ValidationError(
                    "every content unit must reference the source ACL snapshot"
                )
            classification_mismatches = [
                unit.id
                for unit in packet.units
                if unit.classification != packet.source_object.classification
            ]
            if classification_mismatches:
                raise ValidationError(
                    "every content unit must match the source data classification"
                )
            self.state.acl_snapshots[snapshot.id] = snapshot.model_copy(deep=True)
        old_revision_id = self.state.current_revision_by_object.get(
            packet.source_object.id
        )
        old_packet = self.state.packets_by_revision.get(old_revision_id or "")
        if (old_packet and old_packet.revision.sha256 == packet.revision.sha256
                and old_packet.revision.raw_object_uri == packet.revision.raw_object_uri):
            if snapshot is not None:
                old_packet.source_object = packet.source_object.model_copy(deep=True)
                for unit in old_packet.units:
                    unit.acl_snapshot_id = snapshot.id
                    unit.acl_scopes = list(snapshot.scopes)
                    unit.classification = packet.source_object.classification
                view = self.state.artifacts.get(old_packet.artifact.id)
                if view is not None:
                    view.source_object = packet.source_object.model_copy(deep=True)
                self._refresh_assertion_access({unit.id for unit in old_packet.units})
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
        self.state.extraction_packets[stored.extraction.id] = stored
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
    def replace_extraction(
        self,
        context: RequestContext,
        packet: DocumentPacket,
    ) -> IngestResult:
        if packet.workspace_id != context.workspace:
            raise ValidationError("packet workspace does not match request context")
        current_revision_id = self.state.current_revision_by_object.get(packet.source_object.id)
        current = self.state.packets_by_revision.get(current_revision_id or "")
        if (
            current is None
            or current.revision.id != packet.revision.id
            or current.revision.sha256 != packet.revision.sha256
            or current.artifact.id != packet.artifact.id
        ):
            raise ConflictError("candidate does not match the current source revision")

        snapshot = packet.source_object.acl_snapshot
        current_snapshot = current.source_object.acl_snapshot
        if snapshot is None or current_snapshot is None:
            raise ValidationError("source ACL snapshot is required")
        if (
            snapshot.id != current_snapshot.id
            or snapshot.scopes != current_snapshot.scopes
            or packet.source_object.classification != current.source_object.classification
        ):
            raise ConflictError("candidate access controls do not match the current source")
        if packet.extraction.status == "failed" or not packet.units:
            raise ValidationError("only a usable extraction can be activated")
        if (
            packet.revision.object_id != packet.source_object.id
            or packet.artifact.revision_id != packet.revision.id
            or packet.extraction.artifact_id != packet.artifact.id
        ):
            raise ValidationError("candidate extraction references are inconsistent")
        if any(
            unit.extraction_id != packet.extraction.id
            or unit.artifact_id != packet.artifact.id
            or unit.document_id != packet.logical_document.id
            or unit.acl_snapshot_id != snapshot.id
            or unit.acl_scopes != snapshot.scopes
            or unit.classification != packet.source_object.classification
            for unit in packet.units
        ):
            raise ValidationError("candidate units are inconsistent")
        if len({unit.ordinal for unit in packet.units}) != len(packet.units):
            raise ValidationError("candidate unit ordinals must be unique")

        for unit in current.units:
            self.state.units.pop(unit.id, None)
        stored = packet.model_copy(deep=True)
        self.state.packets_by_revision[stored.revision.id] = stored
        self.state.extraction_packets[stored.extraction.id] = stored
        self.state.artifacts[stored.artifact.id] = ArtifactView(
            artifact=stored.artifact,
            document=stored.logical_document,
            source_object=stored.source_object,
            revision=stored.revision,
        )
        for unit in stored.units:
            self.state.units[unit.id] = unit

        return IngestResult(
            status="replaced",
            source_object_id=stored.source_object.id,
            revision_id=stored.revision.id,
            artifact_id=stored.artifact.id,
            document_id=stored.logical_document.id,
            extraction_id=stored.extraction.id,
            unit_count=len(stored.units),
            warnings=list(stored.extraction.warnings),
        )
