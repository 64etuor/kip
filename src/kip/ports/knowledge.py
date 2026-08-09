from __future__ import annotations

from typing import Protocol

from kip.domain.knowledge import KnowledgeEntity
from kip.domain.models import (
    ApprovedAssertion,
    AssertionCandidate,
    GraphEdge,
    GraphNeighborsRequest,
    GraphPath,
    GraphPathRequest,
    RequestContext,
)


class KnowledgeStore(Protocol):
    def save_entity(
        self,
        context: RequestContext,
        entity: KnowledgeEntity,
    ) -> KnowledgeEntity: ...

    def get_entity(
        self,
        context: RequestContext,
        entity_id: str,
    ) -> KnowledgeEntity: ...

    def list_entities(
        self,
        context: RequestContext,
        *,
        limit: int = 100,
    ) -> list[KnowledgeEntity]: ...

    def get_candidate_by_fingerprint(
        self,
        context: RequestContext,
        fingerprint: str,
    ) -> AssertionCandidate | None: ...

    def find_assertions(
        self,
        context: RequestContext,
        *,
        subject_id: str,
        predicate: str,
    ) -> list[ApprovedAssertion]: ...

    def save_candidate(
        self,
        context: RequestContext,
        candidate: AssertionCandidate,
    ) -> AssertionCandidate: ...

    def get_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
    ) -> AssertionCandidate: ...

    def list_candidates(
        self,
        context: RequestContext,
        status: str = "proposed",
        limit: int = 100,
    ) -> list[AssertionCandidate]: ...

    def approve_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> ApprovedAssertion: ...

    def reject_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> AssertionCandidate: ...

    def get_assertion(
        self,
        context: RequestContext,
        assertion_id: str,
    ) -> ApprovedAssertion: ...

    def graph_neighbors(
        self,
        context: RequestContext,
        request: GraphNeighborsRequest,
    ) -> list[GraphEdge]: ...

    def graph_path(
        self,
        context: RequestContext,
        request: GraphPathRequest,
    ) -> list[GraphPath]: ...
