from __future__ import annotations

from datetime import datetime

from kip.domain.identity import AclSnapshot
from kip.domain.interactions import (
    ClarificationQuestion,
    InteractionEvent,
    InteractionFeedback,
    OntologyDiscoveryCandidate,
    UserPreference,
)
from kip.domain.json_types import JsonObject
from kip.domain.knowledge import EntityCandidate, KnowledgeEntity
from kip.domain.models import (
    ApprovedAssertion,
    ArtifactView,
    AssertionCandidate,
    ContentUnit,
    DocumentPacket,
    EmbeddingRecord,
    EmbeddingSpace,
    JobRecord,
)
from kip.domain.telemetry import QueryTrace


class MemoryState:
    def __init__(self) -> None:
        self.packets_by_revision: dict[str, DocumentPacket] = {}
        self.extraction_packets: dict[str, DocumentPacket] = {}
        self.current_revision_by_object: dict[str, str] = {}
        self.units: dict[str, ContentUnit] = {}
        self.artifacts: dict[str, ArtifactView] = {}
        self.documents: dict[str, JsonObject] = {}
        self.entities: dict[str, KnowledgeEntity] = {}
        self.entity_names: dict[str, str] = {}
        self.entity_candidates: dict[str, EntityCandidate] = {}
        self.entity_candidate_ids_by_fingerprint: dict[str, str] = {}
        self.candidates: dict[str, AssertionCandidate] = {}
        self.candidate_ids_by_fingerprint: dict[str, str] = {}
        self.assertions: dict[str, ApprovedAssertion] = {}
        self.jobs: dict[str, JobRecord] = {}
        self.job_order: list[str] = []
        self.job_errors: dict[str, str] = {}
        self.embedding_spaces: dict[str, EmbeddingSpace] = {}
        self.embeddings: dict[tuple[str, str], EmbeddingRecord] = {}
        self.acl_snapshots: dict[str, AclSnapshot] = {}
        self.query_traces: list[tuple[str, QueryTrace]] = []
        self.clarifications: dict[str, tuple[str, str, ClarificationQuestion]] = {}
        self.preferences: dict[tuple[str, str, str], UserPreference] = {}
        self.interaction_feedback: list[tuple[str, str, InteractionFeedback]] = []
        self.ontology_discovery_candidates: dict[
            str, tuple[str, OntologyDiscoveryCandidate]
        ] = {}
        self.ontology_discovery_ids_by_fingerprint: dict[tuple[str, str], str] = {}
        self.interaction_events: list[tuple[str, str, InteractionEvent]] = []

    def document_latest_modified(self, document_id: str) -> datetime | None:
        latest: datetime | None = None
        for revision_id in self.current_revision_by_object.values():
            packet = self.packets_by_revision.get(revision_id)
            if packet is None or packet.logical_document.id != document_id:
                continue
            modified = packet.revision.source_modified_at
            if modified is not None and (latest is None or modified > latest):
                latest = modified
        return latest
