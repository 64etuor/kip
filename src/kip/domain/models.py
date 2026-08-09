from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kip.domain.egress import DataClassification, EgressDecision
from kip.domain.generation import GeneratedClaim, GenerationUsage, ModelRevision
from kip.domain.identity import AclSnapshot
from kip.domain.knowledge import (
    CandidateEvidence,
    EntityCandidate,
    RelationDerivation,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class RequestContext(StrictModel):
    workspace: str = "default"
    principal_id: str = "principal_local"
    acl_scopes: list[str] = Field(default_factory=lambda: ["workspace:default"])
    request_id: str | None = None
    acl_snapshot: AclSnapshot | None = None
    roles: list[str] = Field(default_factory=list)


class EnvelopeMeta(StrictModel):
    request_id: str
    workspace: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    warnings: list[str] = Field(default_factory=list)


class ErrorInfo(StrictModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Envelope(StrictModel):
    schema_version: Literal["kip.envelope.v1"] = "kip.envelope.v1"
    ok: bool
    data: Any = None
    error: ErrorInfo | None = None
    meta: EnvelopeMeta


class SourceObject(StrictModel):
    id: str
    system_id: str
    system_name: str
    system_kind: str
    external_id: str
    object_type: str
    canonical_uri: str
    classification: DataClassification = DataClassification.RESTRICTED
    container_external_id: str | None = None
    acl_scopes: list[str] = Field(default_factory=list)
    acl_snapshot: AclSnapshot | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceRevision(StrictModel):
    id: str
    object_id: str
    revision_key: str
    sha256: str
    size_bytes: int | None = None
    source_modified_at: datetime | None = None
    raw_object_uri: str | None = None
    is_tombstone: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogicalDocument(StrictModel):
    id: str
    stable_key: str
    title: str
    document_type: str | None = None
    family_key: str | None = None
    lifecycle: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Artifact(StrictModel):
    id: str
    revision_id: str
    file_name: str
    extension: str | None = None
    media_type: str | None = None
    byte_size: int | None = None
    sha256: str
    source_path: str | None = None
    cas_uri: str | None = None
    representation_role: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractionRun(StrictModel):
    id: str
    artifact_id: str
    parser_name: str
    parser_version: str
    status: Literal["succeeded", "partial", "failed"]
    quality_score: float | None = None
    output_hash: str | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceLocator(StrictModel):
    type: str
    data: dict[str, Any]


class ContentUnit(StrictModel):
    id: str
    extraction_id: str
    document_id: str | None = None
    artifact_id: str
    ordinal: int
    unit_type: str
    title: str | None = None
    body: str
    body_normalized: str
    lexical_text: str
    locator: EvidenceLocator
    classification: DataClassification = DataClassification.RESTRICTED
    acl_scopes: list[str] = Field(default_factory=list)
    acl_snapshot_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentPacket(StrictModel):
    schema_version: Literal["kip.document-packet.v1"] = "kip.document-packet.v1"
    workspace_id: str
    source_object: SourceObject
    revision: SourceRevision
    logical_document: LogicalDocument
    artifact: Artifact
    extraction: ExtractionRun
    units: list[ContentUnit]


class IngestResult(StrictModel):
    status: Literal["inserted", "unchanged", "replaced", "failed"]
    source_object_id: str
    revision_id: str
    artifact_id: str
    document_id: str
    extraction_id: str | None = None
    unit_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class SearchRequest(StrictModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)
    source_kinds: list[str] = Field(default_factory=list)
    document_types: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    include_candidate_assertions: bool = False


class SearchHit(StrictModel):
    unit_id: str
    document_id: str | None = None
    artifact_id: str
    source_kind: str
    title: str
    snippet: str
    score: float
    locator: EvidenceLocator
    source_uri: str
    source_sha256: str
    source_modified_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmbeddingSpace(StrictModel):
    id: str
    name: str
    provider: str
    model: str
    revision: str
    dimensions: int = Field(gt=0)
    normalized: bool
    status: Literal["inactive", "shadow", "active"] = "inactive"
    configuration: dict[str, Any] = Field(default_factory=dict)


class EmbeddableUnit(StrictModel):
    unit_id: str
    document_id: str | None = None
    title: str
    body_normalized: str
    source_hash: str


class EmbeddingRecord(StrictModel):
    unit_id: str
    embedding: list[float] = Field(min_length=1)
    source_hash: str


class ContextRequest(SearchRequest):
    max_chars: int = Field(default=40000, ge=1000, le=200000)


class ContextItem(StrictModel):
    hit: SearchHit
    body: str
    current_source_sha256: str | None = None
    source_changed_since_index: bool | None = None


class ContextBundle(StrictModel):
    query: str
    items: list[ContextItem]
    total_chars: int
    truncated: bool


class AnswerRequest(ContextRequest):
    max_chars: int = Field(default=12000, ge=1000, le=40000)


class AnswerCitation(StrictModel):
    unit_id: str
    artifact_id: str
    source_uri: str
    locator: EvidenceLocator
    indexed_source_sha256: str
    current_source_sha256: str | None = None
    source_changed_since_index: bool


class AnswerGeneration(StrictModel):
    model: ModelRevision
    usage: GenerationUsage
    provider_request_id: str | None = None


class OntologyAnswerEntity(StrictModel):
    id: str
    entity_type: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)


class OntologyAnswerEdge(StrictModel):
    assertion_id: str
    subject_id: str
    predicate: str
    object_entity_id: str | None = None
    object_value: Any = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    ontology_version: str
    evidence_unit_ids: list[str] = Field(default_factory=list)


class OntologyAnswerPath(StrictModel):
    node_ids: list[str]
    assertion_ids: list[str]
    predicates: list[str]
    depth: int


class OntologyAnswerContext(StrictModel):
    schema_version: Literal["kip.ontology-context.v1"] = "kip.ontology-context.v1"
    entities: list[OntologyAnswerEntity] = Field(default_factory=list)
    edges: list[OntologyAnswerEdge] = Field(default_factory=list)
    paths: list[OntologyAnswerPath] = Field(default_factory=list)
    evidence_unit_ids: list[str] = Field(default_factory=list)


AnswerRefusalReason = Literal[
    "no_admissible_evidence",
    "no_fresh_evidence",
    "exact_xlsx_read_required",
    "insufficient_decision_evidence",
    "model_egress_denied",
    "generation_unavailable",
    "generation_invalid",
]


class AnswerResponse(StrictModel):
    schema_version: Literal["kip.answer.v1"] = "kip.answer.v1"
    query: str
    answer: str
    refused: bool
    refusal_reason: AnswerRefusalReason | None = None
    citations: list[AnswerCitation] = Field(default_factory=list)
    claims: tuple[GeneratedClaim, ...] = ()
    retrieval_mode: Literal["extractive", "generated"] = "extractive"
    generation: AnswerGeneration | None = None
    egress_decision: EgressDecision | None = None
    ontology_context: OntologyAnswerContext | None = None
    warnings: list[str] = Field(default_factory=list)


class ArtifactView(StrictModel):
    artifact: Artifact
    document: LogicalDocument | None = None
    source_object: SourceObject | None = None
    revision: SourceRevision | None = None


class GraphNeighborsRequest(StrictModel):
    node_id: str
    predicates: list[str] = Field(default_factory=list)
    direction: Literal["out", "in", "both"] = "both"
    limit: int = Field(default=100, ge=1, le=1000)
    approved_only: bool = True


class GraphEdge(StrictModel):
    assertion_id: str
    subject_id: str
    predicate: str
    object_entity_id: str | None = None
    object_value: Any = None
    status: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    ontology_version: str
    evidence_unit_ids: list[str] = Field(default_factory=list)


class GraphPathRequest(StrictModel):
    from_node_id: str
    to_node_id: str
    predicates: list[str] = Field(default_factory=list)
    max_depth: int = Field(default=4, ge=1, le=8)
    approved_only: bool = True


class GraphPath(StrictModel):
    node_ids: list[str]
    assertion_ids: list[str]
    predicates: list[str]
    depth: int


class ConnectorEvent(StrictModel):
    schema_version: Literal["kip.connector-event.v1"] = "kip.connector-event.v1"
    event_id: str
    connector_name: str
    operation: Literal["upsert", "delete", "sync_complete"]
    external_id: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)
    acl_scopes: list[str] = Field(default_factory=list)
    acl_snapshot: AclSnapshot | None = None


class AssertionCandidate(StrictModel):
    id: str
    subject_id: str
    predicate: str
    object_entity_id: str | None = None
    object_value: Any = None
    status: Literal["proposed", "approved", "rejected", "superseded"] = "proposed"
    origin: str
    confidence: float | None = None
    ontology_version: str
    evidence: list[CandidateEvidence] = Field(default_factory=list)
    review_note: str | None = None
    fingerprint: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    derivation: RelationDerivation | None = None
    review_risk: Literal["low", "medium", "high"] = "medium"
    contradicts_assertion_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def exactly_one_object(self) -> AssertionCandidate:
        if (self.object_entity_id is None) == (self.object_value is None):
            raise ValueError("exactly one of object_entity_id or object_value is required")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("valid_to must be later than valid_from")
        return self


class OntologyMiningSummary(StrictModel):
    schema_version: Literal["kip.ontology-mining.v1"] = "kip.ontology-mining.v1"
    entity_candidates: list[EntityCandidate] = Field(default_factory=list)
    relation_candidates: list[AssertionCandidate] = Field(default_factory=list)
    model: ModelRevision
    usage: GenerationUsage
    provider_request_id: str | None = None


class OntologyMiningSubmission(StrictModel):
    unit_ids: list[str] = Field(min_length=1, max_length=500)


class ApprovedAssertion(StrictModel):
    id: str
    subject_id: str
    predicate: str
    object_entity_id: str | None = None
    object_value: Any = None
    status: Literal["active", "superseded", "revoked"] = "active"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    ontology_version: str
    source_candidate_id: str | None = None
    acl_scopes: list[str] = Field(default_factory=list)
    evidence_unit_ids: list[str] = Field(default_factory=list)
    evidence_acl_snapshot_ids: list[str] = Field(default_factory=list)


class AssertionExplanation(StrictModel):
    assertion: ApprovedAssertion
    evidence: list[EvidenceRead] = Field(default_factory=list)
    source_candidate: AssertionCandidate | None = None


class Capabilities(StrictModel):
    repository: str
    lexical_search: bool
    semantic_search: bool
    graph_backend: str
    api: bool
    mcp: bool
    parsers: dict[str, str]
    connectors: dict[str, str]
    warnings: list[str] = Field(default_factory=list)


class StatusReport(StrictModel):
    workspace: str
    repository: str
    source_objects: int
    revisions: int
    artifacts: int
    active_extractions: int
    content_units: int
    lexical_units: int
    assertion_candidates: int
    approved_assertions: int
    queued_jobs: int
    failed_jobs: int


class JobRecord(StrictModel):
    id: str
    job_type: str
    payload: dict[str, Any]
    status: str
    attempts: int = 0
    max_attempts: int = 5

class VocabularyItem(StrictModel):
    term: str
    document_frequency: int
    corpus_frequency: int


class SyncSummary(StrictModel):
    source: str
    scanned: int = 0
    inserted: int = 0
    replaced: int = 0
    unchanged: int = 0
    failed: int = 0
    skipped: int = 0
    warnings: list[str] = Field(default_factory=list)


class EvidenceRead(StrictModel):
    unit: ContentUnit
    source_uri: str
    indexed_source_sha256: str
    current_source_sha256: str | None = None
    source_changed_since_index: bool | None = None


class XlsxRangeRead(StrictModel):
    artifact_id: str
    source_uri: str
    sheet: str
    cell_range: str
    cells: list[list[dict[str, Any]]]
    indexed_source_sha256: str
    current_source_sha256: str
    source_changed_since_index: bool
