from __future__ import annotations

from kip.domain.models import (
    ApprovedAssertion,
    AssertionCandidate,
    AssertionCandidateListing,
    AssertionCandidateView,
    AssertionExplanation,
    CandidateEvidencePreview,
    EvidenceRead,
    GraphEdge,
    GraphNeighborsRequest,
    GraphPath,
    GraphPathRequest,
    RequestContext,
)
from kip.errors import NotFoundError, ValidationError
from kip.ontology import OntologyCatalog
from kip.ports.evidence import EvidenceReaderPort
from kip.ports.knowledge import KnowledgeStore

_EVIDENCE_SNIPPET_CHARS = 280


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
            # The review risk is a property of the ontology predicate, not a
            # caller-supplied value: normalize it from the active catalog so
            # manually proposed candidates cannot understate their risk.
            spec = self._ontology.predicate_specs[candidate.predicate]
            candidate = candidate.model_copy(update={"review_risk": spec.risk})
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
        *,
        predicate: str | None = None,
        subject_id: str | None = None,
    ) -> list[AssertionCandidate]:
        return self._store.list_candidates(
            context,
            status,
            limit,
            predicate=predicate,
            subject_id=subject_id,
        )

    def candidate_listing(
        self,
        context: RequestContext,
        status: str = "proposed",
        limit: int = 100,
        *,
        predicate: str | None = None,
        subject_id: str | None = None,
    ) -> AssertionCandidateListing:
        """Review-ready candidate listing with names, labels, and previews.

        Ordered by review risk (high first), then confidence (high first).
        Display names and evidence snippets are resolved with the caller's
        own ACL context, so the listing never reveals text the requesting
        principal could not already read.
        """
        candidates = self._store.list_candidates(
            context,
            status,
            limit,
            predicate=predicate,
            subject_id=subject_id,
        )
        total = self._store.count_candidates(
            context,
            status,
            predicate=predicate,
            subject_id=subject_id,
        )
        entity_names: dict[str, str | None] = {}
        evidence_cache: dict[str, EvidenceRead | None] = {}
        items = [
            self._candidate_view(context, candidate, entity_names, evidence_cache)
            for candidate in candidates
        ]
        return AssertionCandidateListing(
            items=items,
            total=total,
            status=status,
            predicate=predicate,
            subject_id=subject_id,
        )

    def _candidate_view(
        self,
        context: RequestContext,
        candidate: AssertionCandidate,
        entity_names: dict[str, str | None],
        evidence_cache: dict[str, EvidenceRead | None],
    ) -> AssertionCandidateView:
        label_ko = None
        description = None
        if (
            self._ontology is not None
            and candidate.predicate in self._ontology.predicate_specs
        ):
            spec = self._ontology.predicate_specs[candidate.predicate]
            label_ko = spec.label_ko
            description = spec.description
        return AssertionCandidateView(
            **dict(candidate),
            subject_display_name=self._entity_display_name(
                context, candidate.subject_id, entity_names
            ),
            object_display_name=(
                self._entity_display_name(
                    context, candidate.object_entity_id, entity_names
                )
                if candidate.object_entity_id is not None
                else None
            ),
            predicate_label_ko=label_ko,
            predicate_description=description,
            evidence_previews=[
                self._evidence_preview(context, item.content_unit_id, evidence_cache)
                for item in candidate.evidence
            ],
        )

    def _entity_display_name(
        self,
        context: RequestContext,
        entity_id: str,
        cache: dict[str, str | None],
    ) -> str | None:
        if entity_id not in cache:
            try:
                cache[entity_id] = self._store.get_entity(
                    context, entity_id
                ).canonical_name
            except NotFoundError:
                cache[entity_id] = None
        return cache[entity_id]

    def _evidence_preview(
        self,
        context: RequestContext,
        unit_id: str,
        cache: dict[str, EvidenceRead | None],
    ) -> CandidateEvidencePreview:
        if unit_id not in cache:
            try:
                cache[unit_id] = self._evidence.read_unit(
                    context,
                    unit_id,
                    verify_hash=False,
                )
            except NotFoundError:
                cache[unit_id] = None
        read = cache[unit_id]
        if read is None:
            return CandidateEvidencePreview(content_unit_id=unit_id, readable=False)
        return CandidateEvidencePreview(
            content_unit_id=unit_id,
            readable=True,
            title=read.unit.title,
            snippet=read.unit.body[:_EVIDENCE_SNIPPET_CHARS],
        )

    def review_approve(
        self,
        context: RequestContext,
        candidate_id: str,
        note: str | None = None,
        *,
        supersede_contradicted: bool = False,
    ) -> ApprovedAssertion:
        candidate = self._store.get_candidate(context, candidate_id)
        if self._ontology is not None:
            self._ontology.validate_candidate(
                candidate.predicate,
                candidate.ontology_version,
            )
            subject = self._store.get_entity(context, candidate.subject_id)
            object_entity = (
                self._store.get_entity(context, candidate.object_entity_id)
                if candidate.object_entity_id is not None
                else None
            )
            spec = self._ontology.validate_relation(
                subject_type=subject.entity_type,
                predicate=candidate.predicate,
                object_type=(
                    object_entity.entity_type
                    if object_entity is not None
                    else None
                ),
            )
            # Derived from the loaded catalog, never from a hardcoded predicate
            # list, so evidence-free approval fails for every predicate the
            # ontology marks review-required or high-risk.
            if (
                spec.review == "required" or spec.risk == "high"
            ) and not candidate.evidence:
                raise ValidationError(
                    f"predicate {candidate.predicate} requires evidence"
                )
        supersede_ids: tuple[str, ...] = ()
        if supersede_contradicted:
            if not candidate.contradicts_assertion_ids:
                raise ValidationError(
                    "candidate does not contradict any active assertion"
                )
            supersede_ids = tuple(candidate.contradicts_assertion_ids)
        return self._store.approve_candidate(
            context,
            candidate_id,
            context.principal_id,
            note,
            supersede_assertion_ids=supersede_ids,
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

    def revoke_assertion(
        self,
        context: RequestContext,
        assertion_id: str,
        note: str | None,
    ) -> ApprovedAssertion:
        """Transition an approved assertion to `revoked` with a required note.

        Revoked assertions stay stored for audit but are excluded from every
        approved-only consumption path (graph traversal, ontology context,
        contradiction checks, alias-bearing assertion listings).
        """
        if note is None or not note.strip():
            raise ValidationError("a non-empty revocation note is required")
        return self._store.revoke_assertion(
            context,
            assertion_id,
            context.principal_id,
            note.strip(),
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
        return self._store.graph_neighbors(
            context,
            request,
            ontology_version=(
                self._ontology.version if self._ontology is not None else None
            ),
        )

    def graph_path(
        self,
        context: RequestContext,
        request: GraphPathRequest,
    ) -> list[GraphPath]:
        return self._store.graph_path(
            context,
            request,
            ontology_version=(
                self._ontology.version if self._ontology is not None else None
            ),
        )
