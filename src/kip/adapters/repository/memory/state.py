from __future__ import annotations

from kip.domain.identity import AclSnapshot
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
