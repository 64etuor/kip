from __future__ import annotations

from dataclasses import dataclass

from kip.adapters.repository.postgres.database import PostgresDatabase
from kip.domain.knowledge import EntityCandidate, KnowledgeEntity
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

    def save_entity(
        self,
        context: RequestContext,
        entity: KnowledgeEntity,
    ) -> KnowledgeEntity:
        return self.database.save_entity(context, entity)

    def get_entity(
        self,
        context: RequestContext,
        entity_id: str,
    ) -> KnowledgeEntity:
        return self.database.get_entity(context, entity_id)

    def list_entities(
        self,
        context: RequestContext,
        *,
        limit: int = 100,
    ) -> list[KnowledgeEntity]:
        return self.database.list_entities(context, limit=limit)

    def resolve_entities(
        self,
        context: RequestContext,
        normalized_text: str,
        *,
        limit: int = 20,
    ) -> list[KnowledgeEntity]:
        return self.database.resolve_entities(
            context,
            normalized_text,
            limit=limit,
        )

    def save_entity_candidate(
        self,
        context: RequestContext,
        candidate: EntityCandidate,
    ) -> EntityCandidate:
        return self.database.save_entity_candidate(context, candidate)

    def get_entity_candidate_by_fingerprint(
        self,
        context: RequestContext,
        fingerprint: str,
    ) -> EntityCandidate | None:
        return self.database.get_entity_candidate_by_fingerprint(
            context,
            fingerprint,
        )

    def get_entity_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
    ) -> EntityCandidate:
        return self.database.get_entity_candidate(context, candidate_id)

    def list_entity_candidates(
        self,
        context: RequestContext,
        status: str = "proposed",
        limit: int = 100,
    ) -> list[EntityCandidate]:
        return self.database.list_entity_candidates(context, status, limit)

    def approve_entity_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> KnowledgeEntity:
        return self.database.approve_entity_candidate(
            context,
            candidate_id,
            reviewer_id,
            note,
        )

    def reject_entity_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> EntityCandidate:
        return self.database.reject_entity_candidate(
            context,
            candidate_id,
            reviewer_id,
            note,
        )

    def get_candidate_by_fingerprint(
        self,
        context: RequestContext,
        fingerprint: str,
    ) -> AssertionCandidate | None:
        return self.database.get_candidate_by_fingerprint(context, fingerprint)

    def find_assertions(
        self,
        context: RequestContext,
        *,
        subject_id: str,
        predicate: str,
    ) -> list[ApprovedAssertion]:
        return self.database.find_assertions(
            context,
            subject_id=subject_id,
            predicate=predicate,
        )

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
