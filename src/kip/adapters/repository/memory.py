from __future__ import annotations

import json
import math
from collections import deque
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kip.domain.models import (
    ApprovedAssertion,
    ArtifactView,
    AssertionCandidate,
    ContentUnit,
    DocumentPacket,
    EmbeddableUnit,
    EmbeddingRecord,
    EmbeddingSpace,
    GraphEdge,
    GraphNeighborsRequest,
    GraphPath,
    GraphPathRequest,
    IngestResult,
    JobRecord,
    RequestContext,
    SearchHit,
    SearchRequest,
    StatusReport,
    VocabularyItem,
)
from kip.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from kip.ids import new_id

_HIGH_RISK_PREDICATES = {
    "amends",
    "supersedes",
    "approves",
    "authorizes",
    "evidences",
    "satisfies",
    "violates",
}


class MemoryRepository:
    """Deterministic in-memory reference adapter used by tests and local demos.

    It implements the same public repository contract as PostgreSQL, but it is not
    durable and must never be presented as the production data store.
    """

    name = "memory"

    def __init__(self) -> None:
        self.packets_by_revision: dict[str, DocumentPacket] = {}
        self.current_revision_by_object: dict[str, str] = {}
        self.units: dict[str, ContentUnit] = {}
        self.artifacts: dict[str, ArtifactView] = {}
        self.documents: dict[str, dict[str, Any]] = {}
        self.candidates: dict[str, AssertionCandidate] = {}
        self.assertions: dict[str, ApprovedAssertion] = {}
        self.jobs: dict[str, JobRecord] = {}
        self.job_order: list[str] = []
        self._job_errors: dict[str, str] = {}
        self.embedding_spaces: dict[str, EmbeddingSpace] = {}
        self.embeddings: dict[tuple[str, str], EmbeddingRecord] = {}

    def migrate(self, migrations_dir: Path) -> list[str]:
        return [path.name for path in sorted(migrations_dir.glob("*.sql"))]

    def has_revision(self, context: RequestContext, source_object_id: str, sha256: str) -> bool:
        revision_id = self.current_revision_by_object.get(source_object_id)
        if not revision_id:
            return False
        packet = self.packets_by_revision.get(revision_id)
        return bool(packet and packet.revision.sha256 == sha256 and packet.workspace_id == context.workspace)

    def ingest_packet(self, context: RequestContext, packet: DocumentPacket) -> IngestResult:
        if packet.workspace_id != context.workspace:
            raise ValidationError("packet workspace does not match request context")
        old_revision_id = self.current_revision_by_object.get(packet.source_object.id)
        old_packet = self.packets_by_revision.get(old_revision_id or "")
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
                self.units.pop(unit.id, None)

        stored = packet.model_copy(deep=True)
        self.packets_by_revision[stored.revision.id] = stored
        self.current_revision_by_object[stored.source_object.id] = stored.revision.id
        self.documents[stored.logical_document.id] = {
            "document": stored.logical_document.model_dump(mode="json"),
            "artifacts": [stored.artifact.model_dump(mode="json")],
            "current_revision_id": stored.revision.id,
        }
        self.artifacts[stored.artifact.id] = ArtifactView(
            artifact=stored.artifact,
            document=stored.logical_document,
            source_object=stored.source_object,
            revision=stored.revision,
        )
        for unit in stored.units:
            self.units[unit.id] = unit

        status = "replaced" if old_packet else "inserted"
        return IngestResult(
            status=status,
            source_object_id=stored.source_object.id,
            revision_id=stored.revision.id,
            artifact_id=stored.artifact.id,
            document_id=stored.logical_document.id,
            extraction_id=stored.extraction.id,
            unit_count=len(stored.units),
            warnings=list(stored.extraction.warnings),
        )

    def search(self, context: RequestContext, request: SearchRequest, lexemes: str) -> list[SearchHit]:
        raw_terms = [term for term in request.query.lower().split() if term]
        lexical_terms = [term for term in lexemes.lower().split() if term]
        unique_terms = list(dict.fromkeys(raw_terms + lexical_terms))
        scored: list[tuple[float, ContentUnit, ArtifactView]] = []

        for unit in self.units.values():
            if unit.acl_scopes and not set(unit.acl_scopes).issubset(set(context.acl_scopes)):
                continue
            artifact_view = self.artifacts.get(unit.artifact_id)
            if not artifact_view or not artifact_view.source_object or not artifact_view.revision:
                continue
            packet = self.packets_by_revision.get(artifact_view.revision.id)
            if not packet or packet.workspace_id != context.workspace:
                continue
            if request.source_kinds and artifact_view.source_object.system_kind not in request.source_kinds:
                continue
            document_type = artifact_view.document.document_type if artifact_view.document else None
            if request.document_types and document_type not in request.document_types:
                continue
            project_id = (artifact_view.document.metadata or {}).get("project_id") if artifact_view.document else None
            if request.project_ids and project_id not in request.project_ids:
                continue

            haystack = f"{unit.title or ''}\n{unit.lexical_text}\n{artifact_view.artifact.file_name}".lower()
            score = 0.0
            exact_query = request.query.lower()
            if exact_query in haystack:
                score += 12.0
            if exact_query == artifact_view.artifact.file_name.lower():
                score += 30.0
            for term in unique_terms:
                if term in haystack:
                    score += 1.0 + min(3.0, haystack.count(term) * 0.15)
            if score <= 0:
                continue
            scored.append((score, unit, artifact_view))

        scored.sort(key=lambda item: (-item[0], item[1].id))
        hits: list[SearchHit] = []
        for score, unit, view in scored[: request.limit]:
            assert view.source_object is not None
            assert view.revision is not None
            title = unit.title or (view.document.title if view.document else view.artifact.file_name)
            snippet = self._snippet(unit.body, raw_terms or unique_terms)
            hits.append(
                SearchHit(
                    unit_id=unit.id,
                    document_id=unit.document_id,
                    artifact_id=unit.artifact_id,
                    source_kind=view.source_object.system_kind,
                    title=title,
                    snippet=snippet,
                    score=round(score, 4),
                    locator=unit.locator,
                    source_uri=view.source_object.canonical_uri,
                    source_sha256=view.revision.sha256,
                    source_modified_at=view.revision.source_modified_at,
                    metadata={
                        "file_name": view.artifact.file_name,
                        "document_type": view.document.document_type if view.document else None,
                    },
                )
            )
        return hits

    def list_embeddable_units(self, context: RequestContext) -> list[EmbeddableUnit]:
        result: list[EmbeddableUnit] = []
        for unit in self.units.values():
            if unit.acl_scopes and not set(unit.acl_scopes).issubset(context.acl_scopes):
                continue
            view = self.artifacts.get(unit.artifact_id)
            if not view or not view.revision:
                continue
            packet = self.packets_by_revision.get(view.revision.id)
            if not packet or packet.workspace_id != context.workspace:
                continue
            result.append(
                EmbeddableUnit(
                    unit_id=unit.id,
                    document_id=unit.document_id,
                    title=unit.title or (view.document.title if view.document else ""),
                    body_normalized=unit.body_normalized,
                    source_hash=view.revision.sha256,
                )
            )
        return sorted(result, key=lambda item: item.unit_id)

    def save_embedding_space(
        self,
        context: RequestContext,
        space: EmbeddingSpace,
    ) -> EmbeddingSpace:
        existing = next(
            (
                item
                for item in self.embedding_spaces.values()
                if item.name == space.name and item.id != space.id
            ),
            None,
        )
        if existing:
            raise ConflictError(f"embedding space name already exists: {space.name}")
        current = self.embedding_spaces.get(space.id)
        stored = (
            space.model_copy(update={"status": "active"})
            if current and current.status == "active"
            else space
        )
        self.embedding_spaces[space.id] = stored.model_copy(deep=True)
        return self.embedding_spaces[space.id].model_copy(deep=True)

    def active_embedding_space(self, context: RequestContext) -> EmbeddingSpace | None:
        return next(
            (
                space.model_copy(deep=True)
                for space in self.embedding_spaces.values()
                if space.status == "active"
            ),
            None,
        )

    def activate_embedding_space(
        self,
        context: RequestContext,
        space_id: str,
    ) -> EmbeddingSpace:
        if space_id not in self.embedding_spaces:
            raise NotFoundError(f"embedding space not found: {space_id}")
        for current_id, space in list(self.embedding_spaces.items()):
            status = "active" if current_id == space_id else (
                "shadow" if space.status == "active" else space.status
            )
            self.embedding_spaces[current_id] = space.model_copy(update={"status": status})
        return self.embedding_spaces[space_id].model_copy(deep=True)

    def upsert_embeddings(
        self,
        context: RequestContext,
        space_id: str,
        records: list[EmbeddingRecord],
    ) -> int:
        space = self.embedding_spaces.get(space_id)
        if not space:
            raise NotFoundError(f"embedding space not found: {space_id}")
        for record in records:
            if len(record.embedding) != space.dimensions:
                raise ValidationError(
                    f"embedding dimension {len(record.embedding)} does not match {space.dimensions}"
                )
            if record.unit_id not in self.units:
                raise NotFoundError(f"content unit not found: {record.unit_id}")
            self.embeddings[(space_id, record.unit_id)] = record.model_copy(deep=True)
        return len(records)

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            raise ValidationError("query embedding dimension does not match stored vector")
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)

    def vector_search(
        self,
        context: RequestContext,
        request: SearchRequest,
        query_embedding: list[float],
        *,
        space_id: str,
        limit: int,
    ) -> list[SearchHit]:
        space = self.embedding_spaces.get(space_id)
        if not space:
            raise NotFoundError(f"embedding space not found: {space_id}")
        if len(query_embedding) != space.dimensions:
            raise ValidationError("query embedding dimension does not match embedding space")
        scored: list[tuple[float, ContentUnit, ArtifactView]] = []
        for (record_space_id, unit_id), record in self.embeddings.items():
            if record_space_id != space_id:
                continue
            unit = self.units.get(unit_id)
            if not unit or (
                unit.acl_scopes
                and not set(unit.acl_scopes).issubset(context.acl_scopes)
            ):
                continue
            view = self.artifacts.get(unit.artifact_id)
            if not view or not view.source_object or not view.revision:
                continue
            packet = self.packets_by_revision.get(view.revision.id)
            if (
                not packet
                or packet.workspace_id != context.workspace
                or record.source_hash != view.revision.sha256
            ):
                continue
            if request.source_kinds and view.source_object.system_kind not in request.source_kinds:
                continue
            document_type = view.document.document_type if view.document else None
            if request.document_types and document_type not in request.document_types:
                continue
            project_id = (view.document.metadata or {}).get("project_id") if view.document else None
            if request.project_ids and project_id not in request.project_ids:
                continue
            scored.append(
                (
                    self._cosine_similarity(query_embedding, record.embedding),
                    unit,
                    view,
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1].id))
        hits: list[SearchHit] = []
        for rank, (score, unit, view) in enumerate(scored[:limit], start=1):
            assert view.source_object is not None
            assert view.revision is not None
            hits.append(
                SearchHit(
                    unit_id=unit.id,
                    document_id=unit.document_id,
                    artifact_id=unit.artifact_id,
                    source_kind=view.source_object.system_kind,
                    title=unit.title
                    or (view.document.title if view.document else view.artifact.file_name),
                    snippet=self._snippet(unit.body, request.query.split()),
                    score=score,
                    locator=unit.locator,
                    source_uri=view.source_object.canonical_uri,
                    source_sha256=view.revision.sha256,
                    source_modified_at=view.revision.source_modified_at,
                    metadata={
                        "file_name": view.artifact.file_name,
                        "document_type": (
                            view.document.document_type if view.document else None
                        ),
                        "retrieval_channels": ["vector"],
                        "vector_rank": rank,
                    },
                )
            )
        return hits

    def semantic_status(self, context: RequestContext) -> dict[str, Any]:
        active = self.active_embedding_space(context)
        space_vectors = {
            space_id: sum(
                1 for record_space_id, _unit_id in self.embeddings if record_space_id == space_id
            )
            for space_id in self.embedding_spaces
        }
        return {
            "spaces": len(self.embedding_spaces),
            "vectors": len(self.embeddings),
            "active_space": active.model_dump(mode="json") if active else None,
            "space_vectors": space_vectors,
            "space_status": {
                space_id: space.status for space_id, space in self.embedding_spaces.items()
            },
        }

    @staticmethod
    def _snippet(body: str, terms: list[str], width: int = 360) -> str:
        normalized = " ".join(body.split())
        lower = normalized.lower()
        positions = [lower.find(term.lower()) for term in terms if term and lower.find(term.lower()) >= 0]
        start = max(0, min(positions) - width // 3) if positions else 0
        text = normalized[start : start + width]
        if start:
            text = "…" + text
        if start + width < len(normalized):
            text += "…"
        return text

    def vocabulary(self, context: RequestContext, prefix: str, limit: int = 20) -> list[VocabularyItem]:
        needle = prefix.strip().lower()
        counts: dict[str, tuple[int, int]] = {}
        for unit in self.units.values():
            if unit.acl_scopes and not set(unit.acl_scopes).issubset(set(context.acl_scopes)):
                continue
            tokens = unit.lexical_text.lower().split()
            per_doc: set[str] = set()
            for token in tokens:
                if needle and needle not in token:
                    continue
                docs, corpus = counts.get(token, (0, 0))
                counts[token] = (docs, corpus + 1)
                per_doc.add(token)
            for token in per_doc:
                docs, corpus = counts[token]
                counts[token] = (docs + 1, corpus)
        items = [VocabularyItem(term=term, document_frequency=values[0], corpus_frequency=values[1]) for term, values in counts.items()]
        items.sort(key=lambda item: (-item.document_frequency, -item.corpus_frequency, item.term))
        return items[:limit]

    def get_content_unit(self, context: RequestContext, unit_id: str) -> ContentUnit:
        unit = self.units.get(unit_id)
        if not unit:
            raise NotFoundError(f"content unit not found: {unit_id}")
        if unit.acl_scopes and not set(unit.acl_scopes).issubset(set(context.acl_scopes)):
            raise NotFoundError(f"content unit not found: {unit_id}")
        return unit.model_copy(deep=True)

    def get_artifact(self, context: RequestContext, artifact_id: str) -> ArtifactView:
        view = self.artifacts.get(artifact_id)
        if not view:
            raise NotFoundError(f"artifact not found: {artifact_id}")
        scopes = view.source_object.acl_scopes if view.source_object else []
        if scopes and not set(scopes).issubset(set(context.acl_scopes)):
            raise NotFoundError(f"artifact not found: {artifact_id}")
        return view.model_copy(deep=True)

    def get_document(self, context: RequestContext, document_id: str) -> dict[str, Any]:
        document = self.documents.get(document_id)
        if not document:
            raise NotFoundError(f"document not found: {document_id}")
        return deepcopy(document)

    def graph_neighbors(self, context: RequestContext, request: GraphNeighborsRequest) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for assertion in self.assertions.values():
            if assertion.status != "active" and request.approved_only:
                continue
            if assertion.acl_scopes and not set(assertion.acl_scopes).issubset(set(context.acl_scopes)):
                continue
            if request.predicates and assertion.predicate not in request.predicates:
                continue
            matches_out = assertion.subject_id == request.node_id
            matches_in = assertion.object_entity_id == request.node_id
            if request.direction == "out" and not matches_out:
                continue
            if request.direction == "in" and not matches_in:
                continue
            if request.direction == "both" and not (matches_out or matches_in):
                continue
            edges.append(self._edge(assertion))
            if len(edges) >= request.limit:
                break
        return edges

    def graph_path(self, context: RequestContext, request: GraphPathRequest) -> list[GraphPath]:
        adjacency: dict[str, list[tuple[str, ApprovedAssertion]]] = {}
        for assertion in self.assertions.values():
            if request.approved_only and assertion.status != "active":
                continue
            if assertion.object_entity_id is None:
                continue
            if assertion.acl_scopes and not set(assertion.acl_scopes).issubset(set(context.acl_scopes)):
                continue
            if request.predicates and assertion.predicate not in request.predicates:
                continue
            adjacency.setdefault(assertion.subject_id, []).append((assertion.object_entity_id, assertion))
            adjacency.setdefault(assertion.object_entity_id, []).append((assertion.subject_id, assertion))

        queue: deque[tuple[str, list[str], list[str], list[str]]] = deque(
            [(request.from_node_id, [request.from_node_id], [], [])]
        )
        visited_depth: dict[str, int] = {request.from_node_id: 0}
        paths: list[GraphPath] = []
        while queue and len(paths) < 20:
            node, nodes, assertion_ids, predicates = queue.popleft()
            depth = len(assertion_ids)
            if depth >= request.max_depth:
                continue
            for neighbor, assertion in adjacency.get(node, []):
                if neighbor in nodes:
                    continue
                next_nodes = [*nodes, neighbor]
                next_ids = [*assertion_ids, assertion.id]
                next_predicates = [*predicates, assertion.predicate]
                if neighbor == request.to_node_id:
                    paths.append(
                        GraphPath(
                            node_ids=next_nodes,
                            assertion_ids=next_ids,
                            predicates=next_predicates,
                            depth=len(next_ids),
                        )
                    )
                    continue
                next_depth = len(next_ids)
                if visited_depth.get(neighbor, request.max_depth + 1) <= next_depth:
                    continue
                visited_depth[neighbor] = next_depth
                queue.append((neighbor, next_nodes, next_ids, next_predicates))
        return paths

    @staticmethod
    def _edge(assertion: ApprovedAssertion) -> GraphEdge:
        return GraphEdge(
            assertion_id=assertion.id,
            subject_id=assertion.subject_id,
            predicate=assertion.predicate,
            object_entity_id=assertion.object_entity_id,
            object_value=assertion.object_value,
            status=assertion.status,
            valid_from=assertion.valid_from,
            valid_to=assertion.valid_to,
            ontology_version=assertion.ontology_version,
            evidence_unit_ids=list(assertion.evidence_unit_ids),
        )

    def enqueue_job(
        self,
        context: RequestContext,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> str:
        if idempotency_key:
            for job in self.jobs.values():
                if job.payload.get("_idempotency_key") == idempotency_key:
                    if job.status in {"succeeded", "failed"}:
                        job.status = "queued"
                        job.attempts = 0
                        self._job_errors.pop(job.id, None)
                    return job.id
        job_id = new_id("job")
        stored_payload = deepcopy(payload)
        if idempotency_key:
            stored_payload["_idempotency_key"] = idempotency_key
        self.jobs[job_id] = JobRecord(id=job_id, job_type=job_type, payload=stored_payload, status="queued")
        self.job_order.append(job_id)
        return job_id

    def claim_job(self, context: RequestContext, worker_id: str) -> JobRecord | None:
        for job_id in self.job_order:
            job = self.jobs[job_id]
            if job.status != "queued":
                continue
            job.status = "running"
            job.attempts += 1
            job.payload["_worker_id"] = worker_id
            return job.model_copy(deep=True)
        return None

    def complete_job(self, context: RequestContext, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if not job:
            raise NotFoundError(f"job not found: {job_id}")
        job.status = "succeeded"

    def fail_job(self, context: RequestContext, job_id: str, error: str) -> None:
        job = self.jobs.get(job_id)
        if not job:
            raise NotFoundError(f"job not found: {job_id}")
        self._job_errors[job_id] = error
        job.status = "failed" if job.attempts >= job.max_attempts else "queued"

    def list_jobs(self, context: RequestContext, status: str | None = None, limit: int = 100) -> list[JobRecord]:
        result = [job.model_copy(deep=True) for job in self.jobs.values() if not status or job.status == status]
        return result[:limit]

    def add_candidate(self, candidate: AssertionCandidate) -> None:
        self.candidates[candidate.id] = candidate.model_copy(deep=True)

    def save_candidate(self, context: RequestContext, candidate: AssertionCandidate) -> AssertionCandidate:
        self.candidates[candidate.id] = candidate.model_copy(deep=True)
        return candidate.model_copy(deep=True)

    def get_candidate(self, context: RequestContext, candidate_id: str) -> AssertionCandidate:
        candidate = self.candidates.get(candidate_id)
        if not candidate:
            raise NotFoundError(f"candidate not found: {candidate_id}")
        return candidate.model_copy(deep=True)

    def list_candidates(self, context: RequestContext, status: str = "proposed", limit: int = 100) -> list[AssertionCandidate]:
        return [
            candidate.model_copy(deep=True)
            for candidate in self.candidates.values()
            if candidate.status == status
        ][:limit]

    def approve_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> ApprovedAssertion:
        candidate = self.candidates.get(candidate_id)
        if not candidate:
            raise NotFoundError(f"candidate not found: {candidate_id}")
        if candidate.status != "proposed":
            raise ConflictError(f"candidate is already {candidate.status}")
        if candidate.predicate in _HIGH_RISK_PREDICATES and not candidate.evidence:
            raise ValidationError(f"predicate {candidate.predicate} requires evidence")
        evidence_unit_ids = [
            str(item["content_unit_id"])
            for item in candidate.evidence
            if isinstance(item, dict) and item.get("content_unit_id")
        ]
        derived_scopes: set[str] = set()
        for unit_id in evidence_unit_ids:
            unit = self.units.get(unit_id)
            if unit:
                derived_scopes.update(unit.acl_scopes)
        assertion = ApprovedAssertion(
            id=new_id("ast"),
            subject_id=candidate.subject_id,
            predicate=candidate.predicate,
            object_entity_id=candidate.object_entity_id,
            object_value=deepcopy(candidate.object_value),
            ontology_version=candidate.ontology_version,
            source_candidate_id=candidate.id,
            acl_scopes=sorted(derived_scopes) or list(context.acl_scopes),
            evidence_unit_ids=evidence_unit_ids,
        )
        candidate.status = "approved"
        candidate.review_note = note or f"approved by {reviewer_id}"
        self.assertions[assertion.id] = assertion
        return assertion.model_copy(deep=True)

    def reject_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> AssertionCandidate:
        candidate = self.candidates.get(candidate_id)
        if not candidate:
            raise NotFoundError(f"candidate not found: {candidate_id}")
        if candidate.status != "proposed":
            raise ConflictError(f"candidate is already {candidate.status}")
        candidate.status = "rejected"
        candidate.review_note = note or f"rejected by {reviewer_id}"
        return candidate.model_copy(deep=True)

    def get_assertion(self, context: RequestContext, assertion_id: str) -> ApprovedAssertion:
        assertion = self.assertions.get(assertion_id)
        if not assertion:
            raise NotFoundError(f"assertion not found: {assertion_id}")
        if assertion.acl_scopes and not set(assertion.acl_scopes).issubset(set(context.acl_scopes)):
            raise AuthorizationError("assertion is outside the caller access scopes")
        return assertion.model_copy(deep=True)

    def status(self, context: RequestContext) -> StatusReport:
        packets = [packet for packet in self.packets_by_revision.values() if packet.workspace_id == context.workspace]
        current_revision_ids = set(self.current_revision_by_object.values())
        active_packets = [packet for packet in packets if packet.revision.id in current_revision_ids]
        return StatusReport(
            workspace=context.workspace,
            repository=self.name,
            source_objects=len(self.current_revision_by_object),
            revisions=len(packets),
            artifacts=len(self.artifacts),
            active_extractions=len(active_packets),
            content_units=len(self.units),
            lexical_units=len(self.units),
            assertion_candidates=len(self.candidates),
            approved_assertions=len(self.assertions),
            queued_jobs=sum(1 for job in self.jobs.values() if job.status == "queued"),
            failed_jobs=sum(1 for job in self.jobs.values() if job.status == "failed"),
        )

    def rebuild_projection(self, context: RequestContext, projection: str) -> dict[str, Any]:
        if projection not in {"lexical", "graph", "all"}:
            raise ValidationError(f"unsupported projection: {projection}")
        return {
            "projection": projection,
            "status": "rebuilt",
            "content_units": len(self.units),
            "assertions": len(self.assertions),
        }

    def export_canonical(self, context: RequestContext, output: Path) -> dict[str, Any]:
        output.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with output.open("w", encoding="utf-8") as handle:
            for packet in self.packets_by_revision.values():
                if packet.workspace_id != context.workspace:
                    continue
                handle.write(json.dumps({"type": "document_packet", "data": packet.model_dump(mode="json")}, ensure_ascii=False) + "\n")
                count += 1
            for candidate in self.candidates.values():
                handle.write(json.dumps({"type": "assertion_candidate", "data": candidate.model_dump(mode="json")}, ensure_ascii=False) + "\n")
                count += 1
            for assertion in self.assertions.values():
                handle.write(json.dumps({"type": "assertion", "data": assertion.model_dump(mode="json")}, ensure_ascii=False) + "\n")
                count += 1
        return {"output": str(output), "records": count, "generated_at": datetime.now(UTC).isoformat()}
