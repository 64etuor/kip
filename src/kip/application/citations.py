from __future__ import annotations

from kip.domain.egress import EgressDecision
from kip.domain.generation import GenerationResult
from kip.domain.models import (
    AnswerCitation,
    AnswerGeneration,
    AnswerRequest,
    AnswerResponse,
    EvidenceRead,
)
from kip.errors import ValidationError


def citation_from_evidence(item: EvidenceRead) -> AnswerCitation:
    if item.source_changed_since_index is not False:
        raise ValidationError("cannot cite stale or freshness-unverified evidence")
    return AnswerCitation(
        unit_id=item.unit.id,
        artifact_id=item.unit.artifact_id,
        source_uri=item.source_uri,
        locator=item.unit.locator,
        indexed_source_sha256=item.indexed_source_sha256,
        current_source_sha256=item.current_source_sha256,
        source_changed_since_index=False,
    )


def assemble_generated_answer(
    request: AnswerRequest,
    evidence: tuple[EvidenceRead, ...],
    result: GenerationResult,
    decision: EgressDecision,
) -> AnswerResponse:
    evidence_by_id = {item.unit.id: item for item in evidence}
    cited_ids: list[str] = []
    for claim in result.claims:
        if not claim.evidence_ids:
            raise ValidationError("every generated claim must cite exact evidence")
        for evidence_id in claim.evidence_ids:
            if evidence_id not in evidence_by_id:
                raise ValidationError(
                    f"generated claim cites unknown evidence ID: {evidence_id}"
                )
            if evidence_id not in decision.allowed_evidence_ids:
                raise ValidationError(
                    f"generated claim cites evidence not admitted for egress: {evidence_id}"
                )
            if evidence_id not in cited_ids:
                cited_ids.append(evidence_id)
    return AnswerResponse(
        query=request.query,
        answer="\n\n".join(claim.text for claim in result.claims),
        refused=False,
        citations=[citation_from_evidence(evidence_by_id[item]) for item in cited_ids],
        claims=result.claims,
        retrieval_mode="generated",
        generation=AnswerGeneration(
            model=result.model,
            usage=result.usage,
            provider_request_id=result.provider_request_id,
        ),
        egress_decision=decision,
    )
