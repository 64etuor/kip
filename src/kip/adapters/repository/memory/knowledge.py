from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import assert_never

from kip.adapters.repository.memory.acl import assertion_is_visible, unit_is_visible
from kip.adapters.repository.memory.state import MemoryState
from kip.domain.knowledge import (
    CandidateEvidence,
    EntityCandidate,
    KnowledgeEntity,
    normalize_entity_name,
    stable_entity_id,
)
from kip.domain.models import (
    GRAPH_PATH_RESULT_CAP,
    ApprovedAssertion,
    AssertionCandidate,
    GraphEdge,
    GraphNeighborsRequest,
    GraphPath,
    GraphPathRequest,
    RequestContext,
)
from kip.errors import ConflictError, NotFoundError, ValidationError
from kip.ids import new_id
from kip.ontology import FALLBACK_EVIDENCE_REQUIRED_PREDICATES

_REVIEW_RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


def _evidence_required(candidate: AssertionCandidate) -> bool:
    """Fail-closed evidence gate applied at approval time.

    The application layer enforces the catalog-derived rule
    (review == "required" or risk == "high"); this store-level check is a
    defense-in-depth floor using the candidate's own recorded review risk
    plus the fallback predicate set pinned to `ontology/core/predicates.yaml`
    by a contract test.
    """
    return (
        candidate.review_risk == "high"
        or candidate.predicate in FALLBACK_EVIDENCE_REQUIRED_PREDICATES
    )


@dataclass(frozen=True, slots=True)
class MemoryKnowledgeStore:
    state: MemoryState

    def save_entity(
        self,
        context: RequestContext,
        entity: KnowledgeEntity,
    ) -> KnowledgeEntity:
        names = [entity.canonical_name_normalized, *(
            normalize_entity_name(alias) for alias in entity.aliases
        )]
        for name in names:
            existing_id = self.state.entity_names.get(name)
            if existing_id is not None and existing_id != entity.id:
                raise ConflictError(f"entity name or alias already exists: {name}")
        existing = self.state.entities.get(entity.id)
        if existing is not None and existing != entity:
            raise ConflictError(f"entity already exists: {entity.id}")
        self.state.entities[entity.id] = entity.model_copy(deep=True)
        for name in names:
            self.state.entity_names[name] = entity.id
        return entity.model_copy(deep=True)

    def get_entity(
        self,
        context: RequestContext,
        entity_id: str,
    ) -> KnowledgeEntity:
        entity = self.state.entities.get(entity_id)
        if entity is None or not _entity_is_visible(entity, context):
            raise NotFoundError(f"entity not found: {entity_id}")
        return entity.model_copy(deep=True)

    def list_entities(
        self,
        context: RequestContext,
        *,
        limit: int = 100,
    ) -> list[KnowledgeEntity]:
        return [
            entity.model_copy(deep=True)
            for entity in sorted(
                self.state.entities.values(),
                key=lambda item: (item.canonical_name_normalized, item.id),
            )
            if _entity_is_visible(entity, context)
        ][:limit]

    def resolve_entities(
        self,
        context: RequestContext,
        normalized_text: str,
        *,
        limit: int = 20,
    ) -> list[KnowledgeEntity]:
        matches: list[tuple[int, int, str, KnowledgeEntity]] = []
        for entity in self.state.entities.values():
            if not _entity_is_visible(entity, context):
                continue
            names = [entity.canonical_name, *entity.aliases]
            positions = [
                normalized_text.find(normalize_entity_name(name))
                for name in names
                if normalize_entity_name(name) in normalized_text
            ]
            if not positions:
                continue
            longest = max(
                len(normalize_entity_name(name))
                for name in names
                if normalize_entity_name(name) in normalized_text
            )
            matches.append((min(positions), -longest, entity.id, entity))
        matches.sort(key=lambda item: item[:3])
        return [item[3].model_copy(deep=True) for item in matches[:limit]]

    def save_entity_candidate(
        self,
        context: RequestContext,
        candidate: EntityCandidate,
    ) -> EntityCandidate:
        existing_id = self.state.entity_candidate_ids_by_fingerprint.get(
            candidate.fingerprint
        )
        if existing_id is not None:
            return self.get_entity_candidate(context, existing_id)
        if not _candidate_evidence_is_visible(self.state, candidate.evidence, context):
            raise NotFoundError("one or more entity candidate evidence units are unavailable")
        self.state.entity_candidates[candidate.id] = candidate.model_copy(deep=True)
        self.state.entity_candidate_ids_by_fingerprint[candidate.fingerprint] = (
            candidate.id
        )
        return candidate.model_copy(deep=True)

    def get_entity_candidate_by_fingerprint(
        self,
        context: RequestContext,
        fingerprint: str,
    ) -> EntityCandidate | None:
        candidate_id = self.state.entity_candidate_ids_by_fingerprint.get(fingerprint)
        if candidate_id is None:
            return None
        return self.get_entity_candidate(context, candidate_id)

    def get_entity_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
    ) -> EntityCandidate:
        candidate = self.state.entity_candidates.get(candidate_id)
        if candidate is None or not _candidate_evidence_is_visible(
            self.state,
            candidate.evidence,
            context,
        ):
            raise NotFoundError(f"entity candidate not found: {candidate_id}")
        return candidate.model_copy(deep=True)

    def list_entity_candidates(
        self,
        context: RequestContext,
        status: str = "proposed",
        limit: int = 100,
    ) -> list[EntityCandidate]:
        return [
            candidate.model_copy(deep=True)
            for candidate in self.state.entity_candidates.values()
            if candidate.status == status
            and _candidate_evidence_is_visible(self.state, candidate.evidence, context)
        ][:limit]

    def approve_entity_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> KnowledgeEntity:
        candidate = self.get_entity_candidate(context, candidate_id)
        if candidate.status != "proposed":
            raise ConflictError(f"entity candidate is already {candidate.status}")
        derived_scopes = _candidate_evidence_scopes(
            self.state,
            candidate.evidence,
            context,
        )
        entity = KnowledgeEntity(
            id=stable_entity_id(candidate.fingerprint),
            entity_type=candidate.entity_type,
            canonical_name=candidate.canonical_name,
            aliases=candidate.aliases,
            acl_scopes=derived_scopes or list(context.acl_scopes),
            metadata={
                "source_candidate_id": candidate.id,
                "approved_by": reviewer_id,
            },
        )
        saved = self.save_entity(context, entity)
        self.state.entity_candidates[candidate.id] = candidate.model_copy(
            update={
                "status": "approved",
                "review_note": note or f"approved by {reviewer_id}",
            },
            deep=True,
        )
        return saved

    def reject_entity_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> EntityCandidate:
        candidate = self.get_entity_candidate(context, candidate_id)
        if candidate.status != "proposed":
            raise ConflictError(f"entity candidate is already {candidate.status}")
        rejected = candidate.model_copy(
            update={
                "status": "rejected",
                "review_note": note or f"rejected by {reviewer_id}",
            },
            deep=True,
        )
        self.state.entity_candidates[candidate.id] = rejected
        return rejected.model_copy(deep=True)

    def get_candidate_by_fingerprint(
        self,
        context: RequestContext,
        fingerprint: str,
    ) -> AssertionCandidate | None:
        candidate_id = self.state.candidate_ids_by_fingerprint.get(fingerprint)
        if candidate_id is None:
            return None
        candidate = self.state.candidates[candidate_id]
        if not _candidate_evidence_is_visible(
            self.state,
            candidate.evidence,
            context,
        ):
            return None
        return candidate.model_copy(deep=True)

    def find_assertions(
        self,
        context: RequestContext,
        *,
        subject_id: str,
        predicate: str,
    ) -> list[ApprovedAssertion]:
        return [
            assertion.model_copy(deep=True)
            for assertion in self.state.assertions.values()
            if assertion.status == "active"
            and assertion.subject_id == subject_id
            and assertion.predicate == predicate
            and assertion_is_visible(self.state, assertion, context)
        ]

    def list_assertions(
        self,
        context: RequestContext,
        *,
        ontology_version: str,
        predicates: tuple[str, ...],
        limit: int = 10_000,
    ) -> list[ApprovedAssertion]:
        selected = set(predicates)
        return [
            assertion.model_copy(deep=True)
            for assertion in sorted(
                self.state.assertions.values(),
                key=lambda item: item.id,
            )
            if assertion.status == "active"
            and assertion.ontology_version == ontology_version
            and assertion.predicate in selected
            and assertion_is_visible(self.state, assertion, context)
        ][:limit]

    def save_candidate(
        self,
        context: RequestContext,
        candidate: AssertionCandidate,
    ) -> AssertionCandidate:
        if not _candidate_evidence_is_visible(
            self.state,
            candidate.evidence,
            context,
        ):
            raise NotFoundError("one or more candidate evidence units are unavailable")
        if candidate.fingerprint:
            existing_id = self.state.candidate_ids_by_fingerprint.get(
                candidate.fingerprint
            )
            if existing_id is not None:
                return self.get_candidate(context, existing_id)
            self.state.candidate_ids_by_fingerprint[candidate.fingerprint] = candidate.id
        self.state.candidates[candidate.id] = candidate.model_copy(deep=True)
        return candidate.model_copy(deep=True)

    def get_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
    ) -> AssertionCandidate:
        candidate = self.state.candidates.get(candidate_id)
        if not candidate or not _candidate_evidence_is_visible(
            self.state,
            candidate.evidence,
            context,
        ):
            raise NotFoundError(f"candidate not found: {candidate_id}")
        return candidate.model_copy(deep=True)

    def _visible_candidates(
        self,
        context: RequestContext,
        status: str,
        predicate: str | None,
        subject_id: str | None,
    ) -> list[AssertionCandidate]:
        return [
            candidate
            for candidate in self.state.candidates.values()
            if candidate.status == status
            and (predicate is None or candidate.predicate == predicate)
            and (subject_id is None or candidate.subject_id == subject_id)
            and _candidate_evidence_is_visible(self.state, candidate.evidence, context)
        ]

    def list_candidates(
        self,
        context: RequestContext,
        status: str = "proposed",
        limit: int = 100,
        *,
        predicate: str | None = None,
        subject_id: str | None = None,
    ) -> list[AssertionCandidate]:
        selected = self._visible_candidates(context, status, predicate, subject_id)
        selected.sort(
            key=lambda item: (
                _REVIEW_RISK_ORDER.get(item.review_risk, 1),
                # Confidence descending, unknown confidence last.
                -(item.confidence if item.confidence is not None else -1.0),
                item.id,
            )
        )
        return [candidate.model_copy(deep=True) for candidate in selected[:limit]]

    def count_candidates(
        self,
        context: RequestContext,
        status: str = "proposed",
        *,
        predicate: str | None = None,
        subject_id: str | None = None,
    ) -> int:
        return len(self._visible_candidates(context, status, predicate, subject_id))

    def approve_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
        *,
        supersede_assertion_ids: Sequence[str] = (),
    ) -> ApprovedAssertion:
        candidate = self.get_candidate(context, candidate_id)
        if candidate.status != "proposed":
            raise ConflictError(f"candidate is already {candidate.status}")
        if _evidence_required(candidate) and not candidate.evidence:
            raise ValidationError(
                f"predicate {candidate.predicate} requires evidence"
            )
        unknown_supersedes = sorted(
            set(supersede_assertion_ids) - set(candidate.contradicts_assertion_ids)
        )
        if unknown_supersedes:
            raise ValidationError(
                "supersede targets must be contradicted by the candidate: "
                + ", ".join(unknown_supersedes)
            )
        superseded: list[ApprovedAssertion] = []
        for superseded_id in supersede_assertion_ids:
            target = self.state.assertions.get(superseded_id)
            if (
                target is None
                or target.status != "active"
                or not assertion_is_visible(self.state, target, context)
            ):
                raise ConflictError(
                    f"assertion is not active or not visible: {superseded_id}"
                )
            superseded.append(target)
        evidence_unit_ids = [item.content_unit_id for item in candidate.evidence]
        derived_scopes: set[str] = set()
        evidence_snapshot_ids: set[str] = set()
        for unit_id in evidence_unit_ids:
            unit = self.state.units.get(unit_id)
            if unit is None or not unit_is_visible(self.state, unit, context):
                raise NotFoundError("one or more evidence units are unavailable")
            derived_scopes.update(unit.acl_scopes)
            if unit.acl_snapshot_id:
                evidence_snapshot_ids.add(unit.acl_snapshot_id)
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
            evidence_acl_snapshot_ids=sorted(evidence_snapshot_ids),
            valid_from=candidate.valid_from,
            valid_to=candidate.valid_to,
        )
        self.state.candidates[candidate.id] = candidate.model_copy(
            update={
                "status": "approved",
                "review_note": note or f"approved by {reviewer_id}",
            },
            deep=True,
        )
        self.state.assertions[assertion.id] = assertion
        for target in superseded:
            self.state.assertions[target.id] = target.model_copy(
                update={"status": "superseded", "superseded_by": assertion.id},
                deep=True,
            )
        return assertion.model_copy(deep=True)

    def revoke_assertion(
        self,
        context: RequestContext,
        assertion_id: str,
        reviewer_id: str,
        note: str,
    ) -> ApprovedAssertion:
        assertion = self.state.assertions.get(assertion_id)
        if assertion is None or not assertion_is_visible(
            self.state, assertion, context
        ):
            raise NotFoundError(f"assertion not found: {assertion_id}")
        if assertion.status != "active":
            raise ConflictError(f"assertion is already {assertion.status}")
        revoked = assertion.model_copy(
            update={
                "status": "revoked",
                "revoked_at": datetime.now(UTC),
                "revoked_by": reviewer_id,
                "revocation_note": note,
            },
            deep=True,
        )
        self.state.assertions[assertion_id] = revoked
        return revoked.model_copy(deep=True)

    def reject_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> AssertionCandidate:
        candidate = self.get_candidate(context, candidate_id)
        if candidate.status != "proposed":
            raise ConflictError(f"candidate is already {candidate.status}")
        rejected = candidate.model_copy(
            update={
                "status": "rejected",
                "review_note": note or f"rejected by {reviewer_id}",
            },
            deep=True,
        )
        self.state.candidates[candidate.id] = rejected
        return rejected.model_copy(deep=True)

    def get_assertion(
        self,
        context: RequestContext,
        assertion_id: str,
    ) -> ApprovedAssertion:
        assertion = self.state.assertions.get(assertion_id)
        if not assertion:
            raise NotFoundError(f"assertion not found: {assertion_id}")
        if not assertion_is_visible(self.state, assertion, context):
            raise NotFoundError(f"assertion not found: {assertion_id}")
        return assertion.model_copy(deep=True)

    def graph_neighbors(
        self,
        context: RequestContext,
        request: GraphNeighborsRequest,
        *,
        ontology_version: str | None = None,
    ) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        now = datetime.now(UTC)
        for assertion in self.state.assertions.values():
            if request.approved_only and assertion.status != "active":
                continue
            if (
                ontology_version is not None
                and assertion.ontology_version != ontology_version
            ):
                continue
            if not _assertion_is_current(assertion, now):
                continue
            if not assertion_is_visible(self.state, assertion, context):
                continue
            if request.predicates and assertion.predicate not in request.predicates:
                continue
            matches_out = assertion.subject_id == request.node_id
            matches_in = assertion.object_entity_id == request.node_id
            match request.direction:
                case "out":
                    matches = matches_out
                case "in":
                    matches = matches_in
                case "both":
                    matches = matches_out or matches_in
                case unreachable:
                    assert_never(unreachable)
            if not matches:
                continue
            edges.append(_edge(assertion))
            if len(edges) >= request.limit:
                break
        return edges

    def graph_path(
        self,
        context: RequestContext,
        request: GraphPathRequest,
        *,
        ontology_version: str | None = None,
    ) -> list[GraphPath]:
        adjacency: dict[str, list[tuple[str, ApprovedAssertion]]] = {}
        now = datetime.now(UTC)
        for assertion in self.state.assertions.values():
            if request.approved_only and assertion.status != "active":
                continue
            if (
                ontology_version is not None
                and assertion.ontology_version != ontology_version
            ):
                continue
            if not _assertion_is_current(assertion, now):
                continue
            if assertion.object_entity_id is None:
                continue
            if not assertion_is_visible(self.state, assertion, context):
                continue
            if request.predicates and assertion.predicate not in request.predicates:
                continue
            adjacency.setdefault(assertion.subject_id, []).append(
                (assertion.object_entity_id, assertion)
            )
            adjacency.setdefault(assertion.object_entity_id, []).append(
                (assertion.subject_id, assertion)
            )
        return _bounded_paths(adjacency, request)


def _bounded_paths(
    adjacency: dict[str, list[tuple[str, ApprovedAssertion]]],
    request: GraphPathRequest,
) -> list[GraphPath]:
    queue: deque[tuple[str, list[str], list[str], list[str]]] = deque(
        [(request.from_node_id, [request.from_node_id], [], [])]
    )
    visited_depth: dict[str, int] = {request.from_node_id: 0}
    paths: list[GraphPath] = []
    while queue and len(paths) < GRAPH_PATH_RESULT_CAP:
        node, nodes, assertion_ids, predicates = queue.popleft()
        if len(assertion_ids) >= request.max_depth:
            continue
        for neighbor, assertion in adjacency.get(node, []):
            if len(paths) >= GRAPH_PATH_RESULT_CAP:
                break
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


def _entity_is_visible(entity: KnowledgeEntity, context: RequestContext) -> bool:
    return not entity.acl_scopes or set(entity.acl_scopes).issubset(context.acl_scopes)


def _assertion_is_current(
    assertion: ApprovedAssertion,
    now: datetime,
) -> bool:
    if assertion.valid_from is not None and (
        assertion.valid_from.utcoffset() is None or assertion.valid_from > now
    ):
        return False
    return not (
        assertion.valid_to is not None
        and (
            assertion.valid_to.utcoffset() is None
            or assertion.valid_to <= now
        )
    )


def _candidate_evidence_is_visible(
    state: MemoryState,
    evidence: list[CandidateEvidence],
    context: RequestContext,
) -> bool:
    return all(
        (unit := state.units.get(item.content_unit_id)) is not None
        and unit_is_visible(state, unit, context)
        for item in evidence
    )


def _candidate_evidence_scopes(
    state: MemoryState,
    evidence: list[CandidateEvidence],
    context: RequestContext,
) -> list[str]:
    scopes: set[str] = set()
    if not _candidate_evidence_is_visible(state, evidence, context):
        raise NotFoundError("one or more entity candidate evidence units are unavailable")
    for item in evidence:
        unit = state.units[item.content_unit_id]
        scopes.update(unit.acl_scopes)
    return sorted(scopes)
