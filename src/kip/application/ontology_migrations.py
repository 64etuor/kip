from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kip.application.evidence import EvidenceUseCases
from kip.domain.knowledge import (
    CandidateEvidence,
    RelationDerivation,
    intervals_overlap,
    stable_candidate_id,
)
from kip.domain.models import ApprovedAssertion, AssertionCandidate, RequestContext
from kip.errors import ValidationError
from kip.ontology import OntologyCatalog
from kip.ontology_migration import (
    OntologyMigration,
    OntologyMigrationMaterialization,
    SymbolKind,
    diff_ontologies,
    ontology_migration_sha256,
    validate_migration_coverage,
)
from kip.ports.knowledge import KnowledgeStore


class OntologyMigrationUseCases:
    def __init__(
        self,
        store: KnowledgeStore,
        evidence: EvidenceUseCases,
        *,
        domain_profile: str = "research-project",
        max_assertions: int = 10_000,
    ) -> None:
        self._store = store
        self._evidence = evidence
        self._domain_profile = domain_profile
        self._max_assertions = max_assertions

    def materialize(
        self,
        context: RequestContext,
        before_root: Path,
        after_root: Path,
        migration: OntologyMigration,
    ) -> OntologyMigrationMaterialization:
        diff = diff_ontologies(
            before_root,
            after_root,
            before_domain_profile=self._domain_profile,
            after_domain_profile=self._domain_profile,
        )
        errors = validate_migration_coverage(diff, migration)
        if errors:
            raise ValidationError(
                "invalid ontology migration: " + "; ".join(errors)
            )
        self._validate_entity_operations(context, migration)
        target = OntologyCatalog.load(
            after_root,
            domain_profile=self._domain_profile,
        )
        migration_sha256 = ontology_migration_sha256(migration)
        source_predicates = tuple(
            sorted(
                {
                    source
                    for operation in migration.operations
                    if operation.symbol_kind is SymbolKind.PREDICATE
                    for source in operation.sources
                }
            )
        )
        assertions = self._store.list_assertions(
            context,
            ontology_version=migration.from_version,
            predicates=source_predicates,
            limit=self._max_assertions + 1,
        )
        if len(assertions) > self._max_assertions:
            raise ValidationError(
                f"ontology migration exceeds {self._max_assertions} source assertions"
            )
        by_predicate: dict[str, list[ApprovedAssertion]] = {
            predicate: [] for predicate in source_predicates
        }
        for assertion in assertions:
            by_predicate[assertion.predicate].append(assertion)

        source_ids: set[str] = set()
        deprecated_ids: set[str] = set()
        planned: list[AssertionCandidate] = []
        existing_ids: list[str] = []
        for operation in migration.operations:
            if operation.symbol_kind is not SymbolKind.PREDICATE:
                continue
            selected = [
                assertion
                for source in operation.sources
                for assertion in by_predicate.get(source, [])
            ]
            source_ids.update(assertion.id for assertion in selected)
            if selected and not operation.review_required:
                raise ValidationError(
                    "existing assertions require review_required=true"
                )
            if operation.operation == "deprecate":
                deprecated_ids.update(assertion.id for assertion in selected)
                continue
            for assertion in selected:
                for target_predicate in operation.targets:
                    candidate = self._candidate(
                        context,
                        target,
                        migration,
                        migration_sha256,
                        assertion,
                        target_predicate,
                    )
                    existing = self._store.get_candidate_by_fingerprint(
                        context,
                        candidate.fingerprint or "",
                    )
                    if existing is not None:
                        existing_ids.append(existing.id)
                    else:
                        planned.append(candidate)

        saved = [
            self._store.save_candidate(context, candidate)
            for candidate in planned
        ]
        candidate_ids = sorted(
            {candidate.id for candidate in saved} | set(existing_ids)
        )
        return OntologyMigrationMaterialization(
            from_version=migration.from_version,
            to_version=migration.to_version,
            migration_sha256=migration_sha256,
            source_assertion_count=len(source_ids),
            created_candidate_count=len(saved),
            existing_candidate_count=len(existing_ids),
            deprecated_assertion_count=len(deprecated_ids),
            candidate_ids=candidate_ids,
        )

    def _validate_entity_operations(
        self,
        context: RequestContext,
        migration: OntologyMigration,
    ) -> None:
        source_types = {
            source
            for operation in migration.operations
            if operation.symbol_kind is SymbolKind.ENTITY
            for source in operation.sources
        }
        if not source_types:
            return
        entities = self._store.list_entities(
            context,
            limit=self._max_assertions + 1,
        )
        affected = sorted(
            entity.id for entity in entities if entity.entity_type in source_types
        )
        if affected:
            raise ValidationError(
                "entity type migration requires an explicit identity-history "
                "workflow before assertion materialization"
            )

    def _candidate(
        self,
        context: RequestContext,
        target: OntologyCatalog,
        migration: OntologyMigration,
        migration_sha256: str,
        source: ApprovedAssertion,
        target_predicate: str,
    ) -> AssertionCandidate:
        subject = self._store.get_entity(context, source.subject_id)
        object_entity = (
            self._store.get_entity(context, source.object_entity_id)
            if source.object_entity_id is not None
            else None
        )
        spec = target.validate_relation(
            subject_type=subject.entity_type,
            predicate=target_predicate,
            object_type=(
                object_entity.entity_type if object_entity is not None else None
            ),
        )
        evidence = [
            self._candidate_evidence(context, unit_id)
            for unit_id in source.evidence_unit_ids
        ]
        if not evidence:
            raise ValidationError(
                f"source assertion has no evidence: {source.id}"
            )
        derivation = RelationDerivation(
            kind="ontology_migration",
            name=f"{source.predicate}->{target_predicate}",
            revision=f"{migration.from_version}->{migration.to_version}",
            run_id=migration_sha256,
        )
        fingerprint = _migration_candidate_fingerprint(
            migration_sha256=migration_sha256,
            source=source,
            target_predicate=target_predicate,
            evidence=evidence,
        )
        return AssertionCandidate(
            id=stable_candidate_id(fingerprint),
            subject_id=source.subject_id,
            predicate=target_predicate,
            object_entity_id=source.object_entity_id,
            object_value=source.object_value,
            origin=f"ontology_migration:{migration.from_version}->{migration.to_version}",
            ontology_version=migration.to_version,
            evidence=evidence,
            fingerprint=fingerprint,
            valid_from=source.valid_from,
            valid_to=source.valid_to,
            derivation=derivation,
            review_risk=spec.risk,
            contradicts_assertion_ids=self._contradictions(
                context,
                source,
                target_predicate,
            ),
            migrates_assertion_ids=[source.id],
        )

    def _candidate_evidence(
        self,
        context: RequestContext,
        unit_id: str,
    ) -> CandidateEvidence:
        evidence = self._evidence.read_unit(context, unit_id)
        if evidence.source_changed_since_index is not False:
            raise ValidationError(
                f"migration evidence is stale or freshness-unverified: {unit_id}"
            )
        return CandidateEvidence(
            content_unit_id=unit_id,
            source_revision_sha256=evidence.indexed_source_sha256,
            locator=evidence.unit.locator.model_dump(mode="json"),
            quote_hash="sha256:"
            + hashlib.sha256(evidence.unit.body.encode()).hexdigest(),
        )

    def _contradictions(
        self,
        context: RequestContext,
        source: ApprovedAssertion,
        target_predicate: str,
    ) -> list[str]:
        conflicts: list[str] = []
        for assertion in self._store.find_assertions(
            context,
            subject_id=source.subject_id,
            predicate=target_predicate,
        ):
            if not intervals_overlap(
                assertion.valid_from,
                assertion.valid_to,
                source.valid_from,
                source.valid_to,
            ):
                continue
            if (
                assertion.object_entity_id == source.object_entity_id
                and assertion.object_value == source.object_value
            ):
                continue
            conflicts.append(assertion.id)
        return sorted(conflicts)


def _migration_candidate_fingerprint(
    *,
    migration_sha256: str,
    source: ApprovedAssertion,
    target_predicate: str,
    evidence: list[CandidateEvidence],
) -> str:
    payload = {
        "migration_sha256": migration_sha256,
        "source_assertion_id": source.id,
        "subject_id": source.subject_id,
        "target_predicate": target_predicate,
        "object_entity_id": source.object_entity_id,
        "object_value": source.object_value,
        "source_ontology_version": source.ontology_version,
        "valid_from": source.valid_from,
        "valid_to": source.valid_to,
        "evidence": [item.model_dump(mode="json") for item in evidence],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
