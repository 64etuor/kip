from __future__ import annotations

from kip.domain.models import (
    ApprovedAssertion,
    AssertionCandidate,
    AssertionExplanation,
    GraphEdge,
    GraphNeighborsRequest,
    GraphPath,
    GraphPathRequest,
    RequestContext,
)
from kip.errors import NotFoundError
from kip.ontology import OntologyCatalog
from kip.ports.evidence import EvidenceReaderPort
from kip.ports.knowledge import KnowledgeStore


class KnowledgeUseCases:
    def __init__(
        self,
        store: KnowledgeStore,
        evidence: EvidenceReaderPort,
        ontology: OntologyCatalog | None,
    ) -> None:
        self._store = store
        self._evidence = evidence
        self._ontology = ontology

    def create_candidate(
        self,
        context: RequestContext,
        candidate: AssertionCandidate,
    ) -> AssertionCandidate:
        candidate = AssertionCandidate.model_validate(dict(candidate))
        if self._ontology is not None:
            self._ontology.validate_candidate(
                candidate.predicate,
                candidate.ontology_version,
            )
        return self._store.save_candidate(context, candidate)

    def get_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
    ) -> AssertionCandidate:
        return self._store.get_candidate(context, candidate_id)

    def list_candidates(
        self,
        context: RequestContext,
        status: str = "proposed",
        limit: int = 100,
    ) -> list[AssertionCandidate]:
        return self._store.list_candidates(context, status, limit)

    def review_approve(
        self,
        context: RequestContext,
        candidate_id: str,
        note: str | None = None,
    ) -> ApprovedAssertion:
        return self._store.approve_candidate(
            context,
            candidate_id,
            context.principal_id,
            note,
        )

    def review_reject(
        self,
        context: RequestContext,
        candidate_id: str,
        note: str | None = None,
    ) -> AssertionCandidate:
        return self._store.reject_candidate(
            context,
            candidate_id,
            context.principal_id,
            note,
        )

    def get_assertion(
        self,
        context: RequestContext,
        assertion_id: str,
    ) -> ApprovedAssertion:
        return self._store.get_assertion(context, assertion_id)

    def explain_assertion(
        self,
        context: RequestContext,
        assertion_id: str,
    ) -> AssertionExplanation:
        assertion = self._store.get_assertion(context, assertion_id)
        evidence = [
            self._evidence.read_unit(context, unit_id)
            for unit_id in assertion.evidence_unit_ids
        ]
        source_candidate = None
        if assertion.source_candidate_id:
            try:
                source_candidate = self._store.get_candidate(
                    context,
                    assertion.source_candidate_id,
                )
            except NotFoundError:
                source_candidate = None
        return AssertionExplanation(
            assertion=assertion,
            evidence=evidence,
            source_candidate=source_candidate,
        )

    def graph_neighbors(
        self,
        context: RequestContext,
        request: GraphNeighborsRequest,
    ) -> list[GraphEdge]:
        return self._store.graph_neighbors(context, request)

    def graph_path(
        self,
        context: RequestContext,
        request: GraphPathRequest,
    ) -> list[GraphPath]:
        return self._store.graph_path(context, request)
