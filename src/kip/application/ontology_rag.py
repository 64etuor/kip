from __future__ import annotations

import hashlib

from kip.application.evidence import EvidenceUseCases
from kip.domain.knowledge import (
    CandidateEvidence,
    KnowledgeEntity,
    RelationProposal,
    intervals_overlap,
    normalize_entity_name,
    relation_candidate_fingerprint,
    stable_candidate_id,
)
from kip.domain.models import ApprovedAssertion, AssertionCandidate, RequestContext
from kip.errors import ValidationError
from kip.ontology import OntologyCatalog
from kip.ports.knowledge import KnowledgeStore


class OntologyRagUseCases:
    def __init__(
        self,
        store: KnowledgeStore,
        evidence: EvidenceUseCases,
        ontology: OntologyCatalog | None,
    ) -> None:
        self._store = store
        self._evidence = evidence
        self._ontology = ontology

    def create_entity(
        self,
        context: RequestContext,
        entity: KnowledgeEntity,
    ) -> KnowledgeEntity:
        ontology = self._require_ontology()
        ontology.validate_entity_type(entity.entity_type)
        return self._store.save_entity(context, entity)

    def get_entity(
        self,
        context: RequestContext,
        entity_id: str,
    ) -> KnowledgeEntity:
        return self._store.get_entity(context, entity_id)

    def list_entities(
        self,
        context: RequestContext,
        *,
        limit: int = 100,
    ) -> list[KnowledgeEntity]:
        return self._store.list_entities(context, limit=limit)

    def resolve_entities(
        self,
        context: RequestContext,
        text: str,
        *,
        limit: int = 20,
    ) -> list[KnowledgeEntity]:
        normalized = normalize_entity_name(text)
        matches: list[tuple[int, int, str, KnowledgeEntity]] = []
        for entity in self._store.list_entities(context, limit=10_000):
            names = [entity.canonical_name, *entity.aliases]
            positions = [
                normalized.find(normalize_entity_name(name))
                for name in names
                if normalize_entity_name(name) in normalized
            ]
            if not positions:
                continue
            longest = max(
                len(normalize_entity_name(name))
                for name in names
                if normalize_entity_name(name) in normalized
            )
            matches.append((min(positions), -longest, entity.id, entity))
        matches.sort(key=lambda item: item[:3])
        return [item[3] for item in matches[:limit]]

    def propose_relation(
        self,
        context: RequestContext,
        proposal: RelationProposal,
    ) -> AssertionCandidate:
        ontology = self._require_ontology()
        ontology.validate_candidate(proposal.predicate, proposal.ontology_version)
        subject = self._store.get_entity(context, proposal.subject_id)
        object_entity = (
            self._store.get_entity(context, proposal.object_entity_id)
            if proposal.object_entity_id is not None
            else None
        )
        spec = ontology.validate_relation(
            subject_type=subject.entity_type,
            predicate=proposal.predicate,
            object_type=object_entity.entity_type if object_entity else None,
        )
        evidence = tuple(
            self._candidate_evidence(context, unit_id)
            for unit_id in proposal.evidence_unit_ids
        )
        fingerprint = relation_candidate_fingerprint(
            proposal=proposal,
            subject=subject,
            object_entity=object_entity,
            evidence=evidence,
        )
        existing = self._store.get_candidate_by_fingerprint(context, fingerprint)
        if existing is not None:
            return existing
        contradictions = self._contradictions(context, proposal)
        candidate = AssertionCandidate(
            id=stable_candidate_id(fingerprint),
            subject_id=proposal.subject_id,
            predicate=proposal.predicate,
            object_entity_id=proposal.object_entity_id,
            object_value=proposal.object_value,
            origin=f"{proposal.derivation.kind}:{proposal.derivation.name}",
            confidence=proposal.confidence,
            ontology_version=proposal.ontology_version,
            evidence=list(evidence),
            fingerprint=fingerprint,
            valid_from=proposal.valid_from,
            valid_to=proposal.valid_to,
            derivation=proposal.derivation,
            review_risk=spec.risk,
            contradicts_assertion_ids=contradictions,
        )
        return self._store.save_candidate(context, candidate)

    def _candidate_evidence(
        self,
        context: RequestContext,
        unit_id: str,
    ) -> CandidateEvidence:
        evidence = self._evidence.read_unit(context, unit_id)
        if evidence.source_changed_since_index is not False:
            raise ValidationError(
                f"candidate evidence is stale or freshness-unverified: {unit_id}"
            )
        quote_hash = "sha256:" + hashlib.sha256(evidence.unit.body.encode()).hexdigest()
        return CandidateEvidence(
            content_unit_id=unit_id,
            source_revision_sha256=evidence.indexed_source_sha256,
            locator=evidence.unit.locator.model_dump(mode="json"),
            quote_hash=quote_hash,
        )

    def _contradictions(
        self,
        context: RequestContext,
        proposal: RelationProposal,
    ) -> list[str]:
        conflicts: list[str] = []
        for assertion in self._store.find_assertions(
            context,
            subject_id=proposal.subject_id,
            predicate=proposal.predicate,
        ):
            if not intervals_overlap(
                assertion.valid_from,
                assertion.valid_to,
                proposal.valid_from,
                proposal.valid_to,
            ):
                continue
            if _same_object(assertion, proposal):
                continue
            conflicts.append(assertion.id)
        return sorted(conflicts)

    def _require_ontology(self) -> OntologyCatalog:
        if self._ontology is None:
            raise ValidationError("ontology contract is unavailable")
        return self._ontology


def _same_object(assertion: ApprovedAssertion, proposal: RelationProposal) -> bool:
    return (
        assertion.object_entity_id == proposal.object_entity_id
        and assertion.object_value == proposal.object_value
    )
