from __future__ import annotations

from dataclasses import dataclass

from kip.adapters.repository.postgres.database import PostgresDatabase
from kip.domain.models import (
    ApprovedAssertion,
    AssertionCandidate,
    GraphEdge,
    GraphNeighborsRequest,
    GraphPath,
    GraphPathRequest,
    RequestContext,
)


@dataclass(frozen=True, slots=True)
class PostgresKnowledgeStore:
    database: PostgresDatabase

    def save_candidate(
        self,
        context: RequestContext,
        candidate: AssertionCandidate,
    ) -> AssertionCandidate:
        return self.database.save_candidate(context, candidate)

    def get_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
    ) -> AssertionCandidate:
        return self.database.get_candidate(context, candidate_id)

    def list_candidates(
        self,
        context: RequestContext,
        status: str = "proposed",
        limit: int = 100,
    ) -> list[AssertionCandidate]:
        return self.database.list_candidates(context, status, limit)

    def approve_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> ApprovedAssertion:
        return self.database.approve_candidate(
            context,
            candidate_id,
            reviewer_id,
            note,
        )

    def reject_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> AssertionCandidate:
        return self.database.reject_candidate(
            context,
            candidate_id,
            reviewer_id,
            note,
        )

    def get_assertion(
        self,
        context: RequestContext,
        assertion_id: str,
    ) -> ApprovedAssertion:
        return self.database.get_assertion(context, assertion_id)

    def graph_neighbors(
        self,
        context: RequestContext,
        request: GraphNeighborsRequest,
    ) -> list[GraphEdge]:
        return self.database.graph_neighbors(context, request)

    def graph_path(
        self,
        context: RequestContext,
        request: GraphPathRequest,
    ) -> list[GraphPath]:
        return self.database.graph_path(context, request)
