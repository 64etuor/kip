from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from time import perf_counter

from kip.application.egress import EgressPolicyUseCases
from kip.application.evidence import EvidenceUseCases
from kip.application.telemetry import TelemetryUseCases
from kip.domain.generation import GenerationEvidence
from kip.domain.json_types import JsonObject, JsonValue
from kip.domain.knowledge import (
    CandidateEvidence,
    EntityCandidate,
    KnowledgeEntity,
    MinedProposalSkip,
    RelationDerivation,
    RelationMiningRequest,
    RelationProposal,
    entity_candidate_fingerprint,
    intervals_overlap,
    normalize_entity_name,
    relation_candidate_fingerprint,
    stable_candidate_id,
    stable_entity_candidate_id,
)
from kip.domain.models import (
    ApprovedAssertion,
    AssertionCandidate,
    OntologyMiningSummary,
    RequestContext,
)
from kip.domain.telemetry import (
    QueryFilterSummary,
    QueryTrace,
    QueryTraceModelRevision,
    QueryTraceUsage,
    safe_request_id,
)
from kip.errors import ConflictError, NotFoundError, ValidationError
from kip.ontology import OntologyCatalog
from kip.ports.jobs import JobStore
from kip.ports.knowledge import KnowledgeStore
from kip.ports.relation_miner import RelationMinerPort


class OntologyRagUseCases:
    def __init__(
        self,
        store: KnowledgeStore,
        evidence: EvidenceUseCases,
        ontology: OntologyCatalog | None,
        jobs: JobStore,
        egress: EgressPolicyUseCases,
        relation_miner: RelationMinerPort | None = None,
        telemetry: TelemetryUseCases | None = None,
        *,
        max_mining_units: int = 50,
        max_mining_characters: int = 120_000,
        max_entity_proposals: int = 32,
        max_relation_proposals: int = 64,
    ) -> None:
        self._store = store
        self._evidence = evidence
        self._ontology = ontology
        self._jobs = jobs
        self._egress = egress
        self._relation_miner = relation_miner
        self._telemetry = telemetry
        self._max_mining_units = max_mining_units
        self._max_mining_characters = max_mining_characters
        self._max_entity_proposals = max_entity_proposals
        self._max_relation_proposals = max_relation_proposals

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
        if not normalized:
            return []
        return self._store.resolve_entities(
            context,
            normalized,
            limit=limit,
        )

    def get_entity_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
    ) -> EntityCandidate:
        return self._store.get_entity_candidate(context, candidate_id)

    def list_entity_candidates(
        self,
        context: RequestContext,
        *,
        status: str = "proposed",
        limit: int = 100,
    ) -> list[EntityCandidate]:
        return self._store.list_entity_candidates(context, status, limit)

    def approve_entity_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        note: str | None = None,
    ) -> KnowledgeEntity:
        return self._store.approve_entity_candidate(
            context,
            candidate_id,
            context.principal_id,
            note,
        )

    def reject_entity_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        note: str | None = None,
    ) -> EntityCandidate:
        return self._store.reject_entity_candidate(
            context,
            candidate_id,
            context.principal_id,
            note,
        )

    def enqueue_mining(
        self,
        context: RequestContext,
        unit_ids: list[str],
    ) -> str:
        ontology = self._require_ontology()
        miner = self._require_relation_miner()
        selected = _validate_mining_unit_ids(unit_ids, self._max_mining_units)
        digest = hashlib.sha256(
            json.dumps(
                {
                    "workspace": context.workspace,
                    "unit_ids": selected,
                    "principal_id": context.principal_id,
                    "acl_scopes": sorted(context.acl_scopes),
                    "acl_snapshot_id": (
                        context.acl_snapshot.id if context.acl_snapshot else None
                    ),
                    "ontology_version": ontology.version,
                    # Relation proposals can only reference already-approved
                    # entities, so the real curation loop is mine -> approve
                    # entities -> mine again. Binding the digest to the
                    # approved entity set makes the second mining run a new
                    # job instead of deduplicating onto the finished one.
                    "entities_digest": self._entities_digest(context),
                    "miner": {
                        "name": miner.name,
                        "model": miner.model,
                        "revision": miner.revision,
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        payload_unit_ids: list[JsonValue] = list(selected)
        payload_scopes: list[JsonValue] = []
        for scope in sorted(context.acl_scopes):
            payload_scopes.append(scope)
        payload_roles: list[JsonValue] = []
        for role in sorted(context.roles):
            payload_roles.append(role)
        access: JsonObject = {
            "principal_id": context.principal_id,
            "acl_scopes": payload_scopes,
            "roles": payload_roles,
            "acl_snapshot": (
                context.acl_snapshot.model_dump(mode="json")
                if context.acl_snapshot is not None
                else None
            ),
        }
        payload: JsonObject = {
            "workspace": context.workspace,
            "unit_ids": payload_unit_ids,
            "ontology_version": ontology.version,
            "access": access,
        }
        return self._jobs.enqueue_job(
            context,
            "ontology.mine",
            payload,
            f"ontology.mine:{digest}",
        )

    def process_mining(
        self,
        context: RequestContext,
        unit_ids: list[str],
    ) -> OntologyMiningSummary:
        started_at = datetime.now(UTC)
        started = perf_counter()
        try:
            summary = self._process_mining(context, unit_ids)
        except Exception:
            self._record_mining_trace(
                context,
                unit_ids,
                None,
                started_at=started_at,
                duration_ms=(perf_counter() - started) * 1000,
            )
            raise
        self._record_mining_trace(
            context,
            unit_ids,
            summary,
            started_at=started_at,
            duration_ms=(perf_counter() - started) * 1000,
        )
        return summary

    def _process_mining(
        self,
        context: RequestContext,
        unit_ids: list[str],
    ) -> OntologyMiningSummary:
        ontology = self._require_ontology()
        miner = self._require_relation_miner()
        selected = _validate_mining_unit_ids(unit_ids, self._max_mining_units)
        skipped: list[MinedProposalSkip] = []
        reads = []
        for unit_id in selected:
            item = self._evidence.read_unit(context, unit_id)
            if item.source_changed_since_index is not False:
                # Fail closed per unit: a stale or freshness-unverified unit
                # is never mined, but it no longer destroys the whole batch.
                skipped.append(
                    MinedProposalSkip(
                        kind="evidence_unit",
                        reference=item.unit.id,
                        reason="mining evidence is stale or freshness-unverified",
                    )
                )
                continue
            reads.append(item)
        if not reads:
            raise ValidationError(
                "mining evidence is stale or freshness-unverified: "
                + ", ".join(skip.reference for skip in skipped)
            )
        character_count = sum(len(item.unit.body) for item in reads)
        if character_count > self._max_mining_characters:
            raise ValidationError(
                "mining evidence exceeds configured character limit; submit smaller batches"
            )
        decision = self._egress.decide([item.unit for item in reads])
        if not decision.allowed:
            reason = decision.denial_reason.value if decision.denial_reason else "unknown"
            raise ValidationError(f"relation mining egress denied: {reason}")
        generation_evidence = tuple(
            GenerationEvidence(
                id=item.unit.id,
                body=item.unit.body,
                locator=json.dumps(
                    item.unit.locator.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            for item in reads
        )
        result = miner.mine(
            RelationMiningRequest(
                evidence=generation_evidence,
                existing_entities=tuple(
                    self._store.list_entities(context, limit=10_000)
                ),
                ontology_version=ontology.version,
                max_entity_proposals=self._max_entity_proposals,
                max_relation_proposals=self._max_relation_proposals,
            )
        )
        skipped.extend(result.skipped)
        if result.model.model != miner.model or result.model.revision != miner.revision:
            raise ValidationError(
                "relation mining result model revision does not match the configured miner"
            )
        if len(result.entities) > self._max_entity_proposals:
            raise ValidationError("entity proposal count exceeds configured limit")
        if len(result.relations) > self._max_relation_proposals:
            raise ValidationError("relation proposal count exceeds configured limit")
        allowed_evidence_ids = {item.unit.id for item in reads}
        exact_evidence = {
            item.unit.id: CandidateEvidence(
                content_unit_id=item.unit.id,
                source_revision_sha256=item.indexed_source_sha256,
                locator=item.unit.locator.model_dump(mode="json"),
                quote_hash="sha256:"
                + hashlib.sha256(item.unit.body.encode()).hexdigest(),
            )
            for item in reads
        }
        derivation = RelationDerivation(
            kind="model",
            name=miner.name,
            model=miner.model,
            revision=miner.revision,
        )
        entity_candidates: list[EntityCandidate] = []
        for proposal in result.entities:
            reference = f"{proposal.entity_type}:{proposal.canonical_name}"
            try:
                ontology.validate_entity_type(proposal.entity_type)
                _require_known_evidence(
                    proposal.evidence_ids, allowed_evidence_ids
                )
            except ValidationError as exc:
                skipped.append(
                    MinedProposalSkip(
                        kind="entity", reference=reference, reason=str(exc)
                    )
                )
                continue
            evidence = tuple(exact_evidence[item] for item in proposal.evidence_ids)
            fingerprint = entity_candidate_fingerprint(
                proposal,
                ontology_version=ontology.version,
                evidence=evidence,
                derivation=derivation,
            )
            existing = self._store.get_entity_candidate_by_fingerprint(
                context,
                fingerprint,
            )
            if existing is not None:
                entity_candidates.append(existing)
                continue
            try:
                entity_candidates.append(
                    self._store.save_entity_candidate(
                        context,
                        EntityCandidate(
                            id=stable_entity_candidate_id(fingerprint),
                            fingerprint=fingerprint,
                            entity_type=proposal.entity_type,
                            canonical_name=proposal.canonical_name,
                            aliases=proposal.aliases,
                            origin=f"model:{miner.name}",
                            confidence=proposal.confidence,
                            ontology_version=ontology.version,
                            evidence=list(evidence),
                            derivation=derivation,
                        ),
                    )
                )
            except (ValidationError, NotFoundError, ConflictError) as exc:
                skipped.append(
                    MinedProposalSkip(
                        kind="entity", reference=reference, reason=str(exc)
                    )
                )
        relation_candidates: list[AssertionCandidate] = []
        for relation_proposal in result.relations:
            reference = (
                f"{relation_proposal.subject_entity_id} "
                f"{relation_proposal.predicate} "
                f"{relation_proposal.object_entity_id}"
            )
            try:
                ontology.validate_candidate(
                    relation_proposal.predicate, ontology.version
                )
                _require_known_evidence(
                    relation_proposal.evidence_ids, allowed_evidence_ids
                )
                relation_candidates.append(
                    self.propose_relation(
                        context,
                        RelationProposal(
                            subject_id=relation_proposal.subject_entity_id,
                            predicate=relation_proposal.predicate,
                            object_entity_id=relation_proposal.object_entity_id,
                            ontology_version=ontology.version,
                            evidence_unit_ids=relation_proposal.evidence_ids,
                            confidence=relation_proposal.confidence,
                            valid_from=relation_proposal.valid_from,
                            valid_to=relation_proposal.valid_to,
                            derivation=derivation,
                        ),
                    )
                )
            except (ValidationError, NotFoundError, ConflictError) as exc:
                skipped.append(
                    MinedProposalSkip(
                        kind="relation", reference=reference, reason=str(exc)
                    )
                )
        summary = OntologyMiningSummary(
            entity_candidates=entity_candidates,
            relation_candidates=relation_candidates,
            skipped=skipped,
            model=result.model,
            usage=result.usage,
            provider_request_id=result.provider_request_id,
        )
        self._record_job_result(context, summary)
        return summary

    def _entities_digest(self, context: RequestContext) -> str:
        entities = sorted(
            (
                entity.id,
                entity.entity_type,
                entity.canonical_name_normalized,
                sorted(normalize_entity_name(alias) for alias in entity.aliases),
            )
            for entity in self._store.list_entities(context, limit=10_000)
        )
        return hashlib.sha256(
            json.dumps(
                entities,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    def _record_job_result(
        self,
        context: RequestContext,
        summary: OntologyMiningSummary,
    ) -> None:
        """Attach the mining outcome to the durable job for status inspection."""
        job_id = context.request_id
        if not job_id or not job_id.startswith("job_"):
            return
        try:
            self._jobs.record_job_result(
                context,
                job_id,
                {
                    "schema_version": "kip.ontology-mining-result.v1",
                    "entity_candidate_ids": [
                        item.id for item in summary.entity_candidates
                    ],
                    "relation_candidate_ids": [
                        item.id for item in summary.relation_candidates
                    ],
                    "skipped": [
                        item.model_dump(mode="json") for item in summary.skipped
                    ],
                },
            )
        except NotFoundError:
            # process_mining may run outside a durable job (direct call with
            # an opaque request ID); the summary itself is still returned.
            return

    def _record_mining_trace(
        self,
        context: RequestContext,
        unit_ids: list[str],
        summary: OntologyMiningSummary | None,
        *,
        started_at: datetime,
        duration_ms: float,
    ) -> None:
        if self._telemetry is None:
            return
        models = (
            [
                QueryTraceModelRevision(
                    role="relation_miner",
                    provider=summary.model.provider,
                    model=summary.model.model,
                    revision=summary.model.revision,
                )
            ]
            if summary is not None
            else []
        )
        usage = (
            QueryTraceUsage.model_validate(summary.usage.model_dump(mode="json"))
            if summary is not None
            else None
        )
        self._telemetry.record(
            context,
            QueryTrace(
                request_id=safe_request_id(context.request_id),
                route="ontology_mining",
                outcome="succeeded" if summary is not None else "failed",
                started_at=started_at,
                duration_ms=duration_ms,
                filters=QueryFilterSummary(limit=min(max(len(unit_ids), 1), 1000)),
                stages=[
                    "acl_prefilter",
                    "exact_evidence_read",
                    "model_egress_policy",
                    "structured_generation",
                    "candidate_persistence",
                ],
                selected_evidence_ids=list(dict.fromkeys(unit_ids[:100])),
                acl_policy_version=(
                    context.acl_snapshot.version
                    if context.acl_snapshot is not None
                    else None
                ),
                models=models,
                warnings=(
                    ["ontology_mining_failed"]
                    if summary is None
                    else (
                        ["ontology_mining_skipped_proposals"]
                        if summary.skipped
                        else []
                    )
                ),
                usage=usage,
            ),
        )

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

    def _require_relation_miner(self) -> RelationMinerPort:
        if self._relation_miner is None:
            raise ValidationError("relation miner is not configured")
        return self._relation_miner


def _same_object(assertion: ApprovedAssertion, proposal: RelationProposal) -> bool:
    return (
        assertion.object_entity_id == proposal.object_entity_id
        and assertion.object_value == proposal.object_value
    )


def _validate_mining_unit_ids(unit_ids: list[str], limit: int) -> list[str]:
    if not unit_ids:
        raise ValidationError("at least one evidence unit ID is required")
    if len(unit_ids) > limit:
        raise ValidationError(f"at most {limit} evidence units may be mined at once")
    if any(not item.strip() for item in unit_ids):
        raise ValidationError("evidence unit IDs must not be blank")
    if len(unit_ids) != len(set(unit_ids)):
        raise ValidationError("evidence unit IDs must be unique")
    return sorted(unit_ids)


def _require_known_evidence(
    evidence_ids: tuple[str, ...],
    allowed: set[str],
) -> None:
    unknown = sorted(set(evidence_ids) - allowed)
    if unknown:
        raise ValidationError("unknown mining evidence IDs: " + ", ".join(unknown))
