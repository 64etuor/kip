from __future__ import annotations

from dataclasses import dataclass

from kip.adapters.repository.postgres.database import PostgresDatabase
from kip.domain.egress import DataClassification
from kip.domain.identity import AclSnapshot
from kip.domain.models import DocumentPacket, IngestResult, RequestContext


@dataclass(frozen=True, slots=True)
class PostgresIngestionStore:
    database: PostgresDatabase

    def upsert_acl_snapshot(
        self,
        context: RequestContext,
        source_object_id: str,
        snapshot: AclSnapshot,
        classification: DataClassification,
    ) -> None:
        self.database.upsert_acl_snapshot(
            context,
            source_object_id,
            snapshot,
            classification,
        )

    def has_revision(
        self,
        context: RequestContext,
        source_object_id: str,
        sha256: str,
    ) -> bool:
        return self.database.has_revision(context, source_object_id, sha256)

    def ingest_packet(
        self,
        context: RequestContext,
        packet: DocumentPacket,
    ) -> IngestResult:
        return self.database.ingest_packet(context, packet)

    def replace_extraction(
        self,
        context: RequestContext,
        packet: DocumentPacket,
    ) -> IngestResult:
        return self.database.replace_extraction(context, packet)
