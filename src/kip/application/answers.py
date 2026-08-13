from __future__ import annotations

from kip.application.answer_adequacy import prepare_answer_evidence
from kip.application.citations import citation_from_evidence
from kip.domain.models import (
    AnswerRequest,
    AnswerResponse,
    EvidenceRead,
    OntologyAnswerContext,
)


def assemble_extractive_answer(
    request: AnswerRequest,
    evidence: tuple[EvidenceRead, ...],
    ontology_context: OntologyAnswerContext | None = None,
) -> AnswerResponse:
    remaining = request.max_chars
    passages: list[str] = []
    citations = []
    # An extract quotes one document. Concatenating passages from several
    # documents produced a body that read like a synthesized answer while
    # silently mixing unrelated sources; the generation path is what
    # combines sources, and it validates every claim against its citation.
    if evidence:
        leading_document = evidence[0].unit.document_id
        evidence = tuple(item for item in evidence if item.unit.document_id == leading_document)
    for item in evidence:
        passage = item.unit.body[:remaining]
        if not passage:
            break
        passages.append(passage)
        citations.append(citation_from_evidence(item))
        remaining -= len(passage)
        if remaining <= 0:
            break
    if ontology_context is not None:
        cited_ids = {item.unit_id for item in citations}
        evidence_by_id = {item.unit.id: item for item in evidence}
        for unit_id in ontology_context.evidence_unit_ids:
            if unit_id in cited_ids or unit_id not in evidence_by_id:
                continue
            citations.append(citation_from_evidence(evidence_by_id[unit_id]))
            cited_ids.add(unit_id)
    return AnswerResponse(
        query=request.query,
        answer="\n\n".join(passages),
        refused=False,
        citations=citations,
        ontology_context=ontology_context,
    )


def assemble_answer(
    request: AnswerRequest,
    evidence: list[EvidenceRead],
    *,
    had_stale_evidence: bool,
) -> AnswerResponse:
    prepared = prepare_answer_evidence(
        request,
        evidence,
        had_stale_evidence=had_stale_evidence,
    )
    if prepared.refusal is not None:
        return prepared.refusal
    return assemble_extractive_answer(request, prepared.evidence)
