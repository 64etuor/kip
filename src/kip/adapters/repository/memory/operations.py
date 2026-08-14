from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from kip.adapters.repository.memory.state import MemoryState
from kip.domain.json_types import JsonObject
from kip.domain.models import RequestContext, StatusReport
from kip.errors import ValidationError


@dataclass(frozen=True, slots=True)
class MemoryOperationsStore:
    state: MemoryState
    name: ClassVar[str] = "memory"

    def ping(self) -> None:
        # The in-memory store is reachable whenever the process is alive.
        return None

    def migrate(self, migrations_dir: Path) -> list[str]:
        return [path.name for path in sorted(migrations_dir.glob("*.sql"))]

    def status(self, context: RequestContext) -> StatusReport:
        packets = [
            packet
            for packet in self.state.packets_by_revision.values()
            if packet.workspace_id == context.workspace
        ]
        current_revision_ids = set(self.state.current_revision_by_object.values())
        active_packets = [
            packet
            for packet in packets
            if packet.revision.id in current_revision_ids
        ]
        return StatusReport(
            workspace=context.workspace,
            repository=self.name,
            source_objects=len(self.state.current_revision_by_object),
            revisions=len(packets),
            artifacts=len(self.state.artifacts),
            active_extractions=len(active_packets),
            content_units=len(self.state.units),
            lexical_units=len(self.state.units),
            assertion_candidates=len(self.state.candidates),
            approved_assertions=len(self.state.assertions),
            queued_jobs=sum(
                1 for job in self.state.jobs.values() if job.status == "queued"
            ),
            failed_jobs=sum(
                1 for job in self.state.jobs.values() if job.status == "failed"
            ),
        )

    def rebuild_projection(
        self,
        context: RequestContext,
        projection: str,
    ) -> JsonObject:
        if projection not in {"lexical", "graph", "all"}:
            raise ValidationError(f"unsupported projection: {projection}")
        return {
            "projection": projection,
            "status": "rebuilt",
            "content_units": len(self.state.units),
            "assertions": len(self.state.assertions),
        }

    def export_canonical(
        self,
        context: RequestContext,
        output: Path,
    ) -> JsonObject:
        output.parent.mkdir(parents=True, exist_ok=True)
        records: list[JsonObject] = []
        records.extend(
            {
                "type": "document_packet",
                "data": packet.model_dump(mode="json"),
            }
            for packet in self.state.packets_by_revision.values()
            if packet.workspace_id == context.workspace
        )
        records.extend(
            {
                "type": "assertion_candidate",
                "data": candidate.model_dump(mode="json"),
            }
            for candidate in self.state.candidates.values()
        )
        records.extend(
            {
                "type": "assertion",
                "data": assertion.model_dump(mode="json"),
            }
            for assertion in self.state.assertions.values()
        )
        with output.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {
            "output": str(output),
            "records": len(records),
            "generated_at": datetime.now(UTC).isoformat(),
        }
