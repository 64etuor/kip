from __future__ import annotations

from dataclasses import dataclass

from kip.adapters.repository.postgres.database import PostgresDatabase
from kip.domain.models import DocumentPacket, IngestResult, RequestContext


@dataclass(frozen=True, slots=True)
class PostgresIngestionStore:
    database: PostgresDatabase

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
