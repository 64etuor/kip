from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from typing import Final, assert_never

from kip.adapters.repository.memory.state import MemoryState
from kip.domain.models import (
    ApprovedAssertion,
    AssertionCandidate,
    GraphEdge,
    GraphNeighborsRequest,
    GraphPath,
    GraphPathRequest,
    RequestContext,
)
from kip.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from kip.ids import new_id


_HIGH_RISK_PREDICATES: Final = {
    "amends",
    "supersedes",
    "approves",
    "authorizes",
    "evidences",
    "satisfies",
    "violates",
}


@dataclass(frozen=True, slots=True)
class MemoryKnowledgeStore:
    state: MemoryState

    def save_candidate(
        self,
        context: RequestContext,
        candidate: AssertionCandidate,
    ) -> AssertionCandidate:
        self.state.candidates[candidate.id] = candidate.model_copy(deep=True)
        return candidate.model_copy(deep=True)

    def get_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
    ) -> AssertionCandidate:
        candidate = self.state.candidates.get(candidate_id)
        if not candidate:
            raise NotFoundError(f"candidate not found: {candidate_id}")
        return candidate.model_copy(deep=True)

    def list_candidates(
        self,
        context: RequestContext,
        status: str = "proposed",
        limit: int = 100,
    ) -> list[AssertionCandidate]:
        return [
            candidate.model_copy(deep=True)
            for candidate in self.state.candidates.values()
            if candidate.status == status
        ][:limit]

    def approve_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> ApprovedAssertion:
        candidate = self.state.candidates.get(candidate_id)
        if not candidate:
            raise NotFoundError(f"candidate not found: {candidate_id}")
        if candidate.status != "proposed":
            raise ConflictError(f"candidate is already {candidate.status}")
        if candidate.predicate in _HIGH_RISK_PREDICATES and not candidate.evidence:
            raise ValidationError(
                f"predicate {candidate.predicate} requires evidence"
            )
        evidence_unit_ids = [
            str(item["content_unit_id"])
            for item in candidate.evidence
            if isinstance(item, dict) and item.get("content_unit_id")
        ]
        derived_scopes: set[str] = set()
        for unit_id in evidence_unit_ids:
            unit = self.state.units.get(unit_id)
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
        self.state.assertions[assertion.id] = assertion
        return assertion.model_copy(deep=True)

    def reject_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> AssertionCandidate:
        candidate = self.state.candidates.get(candidate_id)
        if not candidate:
            raise NotFoundError(f"candidate not found: {candidate_id}")
        if candidate.status != "proposed":
            raise ConflictError(f"candidate is already {candidate.status}")
        candidate.status = "rejected"
        candidate.review_note = note or f"rejected by {reviewer_id}"
        return candidate.model_copy(deep=True)

    def get_assertion(
        self,
        context: RequestContext,
        assertion_id: str,
    ) -> ApprovedAssertion:
        assertion = self.state.assertions.get(assertion_id)
        if not assertion:
            raise NotFoundError(f"assertion not found: {assertion_id}")
        if assertion.acl_scopes and not set(assertion.acl_scopes).issubset(
            set(context.acl_scopes)
        ):
            raise AuthorizationError(
                "assertion is outside the caller access scopes"
            )
        return assertion.model_copy(deep=True)

    def graph_neighbors(
        self,
        context: RequestContext,
        request: GraphNeighborsRequest,
    ) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for assertion in self.state.assertions.values():
            if request.approved_only and assertion.status != "active":
                continue
            if assertion.acl_scopes and not set(assertion.acl_scopes).issubset(
                set(context.acl_scopes)
            ):
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
    ) -> list[GraphPath]:
        adjacency: dict[str, list[tuple[str, ApprovedAssertion]]] = {}
        for assertion in self.state.assertions.values():
            if request.approved_only and assertion.status != "active":
                continue
            if assertion.object_entity_id is None:
                continue
            if assertion.acl_scopes and not set(assertion.acl_scopes).issubset(
                set(context.acl_scopes)
            ):
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
    while queue and len(paths) < 20:
        node, nodes, assertion_ids, predicates = queue.popleft()
        if len(assertion_ids) >= request.max_depth:
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
