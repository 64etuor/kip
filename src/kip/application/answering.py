from __future__ import annotations

import json

from kip.application.answers import (
    assemble_extractive_answer,
    prepare_answer_evidence,
)
from kip.application.citations import assemble_generated_answer
from kip.application.egress import EgressPolicyUseCases
from kip.application.evidence import EvidenceUseCases
from kip.application.ontology_context import OntologyContextUseCases
from kip.application.search import RetrievalUseCases
from kip.domain.egress import EgressDecision
from kip.domain.generation import (
    GenerationEvidence,
    GenerationRelation,
    GenerationRequest,
    validate_generation_result,
)
from kip.domain.models import (
    AnswerRefusalReason,
    AnswerRequest,
    AnswerResponse,
    EvidenceRead,
    OntologyAnswerContext,
    RequestContext,
)
from kip.errors import ConfigurationError, DependencyUnavailableError, ValidationError
from kip.ports.generation import GenerationPort
from kip.settings import Settings


class AnsweringUseCases:
    def __init__(
        self,
        settings: Settings,
        retrieval: RetrievalUseCases,
        evidence: EvidenceUseCases,
        egress: EgressPolicyUseCases,
        generator: GenerationPort | None,
        ontology_context: OntologyContextUseCases,
    ) -> None:
        raw = settings.get("models.generation", {}) or {}
        if not isinstance(raw, dict):
            raise ConfigurationError("models.generation must be a table")
        fallback = raw.get("fallback_on_error", False)
        if not isinstance(fallback, bool):
            raise ConfigurationError("models.generation.fallback_on_error must be boolean")
        self._enabled = bool(raw.get("enabled", False))
        self._fallback_on_error = fallback
        try:
            self._max_claims = int(str(raw.get("max_claims", 8)))
            self._max_output_tokens = int(str(raw.get("max_output_tokens", 1024)))
        except ValueError as error:
            raise ConfigurationError(
                "generation claim and token limits must be integers"
            ) from error
        if not 1 <= self._max_claims <= 64:
            raise ConfigurationError("models.generation.max_claims must be between 1 and 64")
        if not 64 <= self._max_output_tokens <= 32768:
            raise ConfigurationError(
                "models.generation.max_output_tokens must be between 64 and 32768"
            )
        self._retrieval = retrieval
        self._evidence = evidence
        self._egress = egress
        self._generator = generator
        self._ontology_context = ontology_context
        if (
            generator is not None
            and egress.policy.provider is not None
            and generator.provider != egress.policy.provider.value
        ):
            raise ConfigurationError(
                "generation adapter provider does not match the egress policy"
            )

    def answer(
        self,
        context: RequestContext,
        request: AnswerRequest,
    ) -> AnswerResponse:
        hits = self._retrieval.search(context, request)
        ontology_bundle = self._ontology_context.build(context, request.query)
        fresh: list[EvidenceRead] = list(ontology_bundle.evidence)
        seen_ids = {item.unit.id for item in fresh}
        had_stale_evidence = False
        for hit in hits:
            if hit.unit_id in seen_ids:
                continue
            item = self._evidence.read_unit(context, hit.unit_id)
            if item.source_changed_since_index:
                had_stale_evidence = True
                continue
            fresh.append(item)
            seen_ids.add(item.unit.id)
        had_stale_evidence = (
            had_stale_evidence or ontology_bundle.had_stale_evidence
        )
        prepared = prepare_answer_evidence(
            request,
            fresh,
            had_stale_evidence=had_stale_evidence,
            ontology_evidence_ids=set(
                ontology_bundle.context.evidence_unit_ids
                if ontology_bundle.context is not None
                else []
            ),
        )
        if prepared.refusal is not None:
            if not _context_is_cited(
                ontology_bundle.context,
                {item.unit_id for item in prepared.refusal.citations},
            ):
                return prepared.refusal
            return prepared.refusal.model_copy(
                update={"ontology_context": ontology_bundle.context}
            )
        extractive = assemble_extractive_answer(
            request,
            prepared.evidence,
            ontology_bundle.context,
        )
        if not self._enabled:
            return extractive
        if self._generator is None:
            return self._generation_refusal(
                request,
                "generation_unavailable",
                "구조화 생성기가 구성되지 않아 답변을 확정하지 않았습니다.",
            )

        decision = self._egress.decide([item.unit for item in prepared.evidence])
        if not decision.allowed:
            return AnswerResponse(
                query=request.query,
                answer="모델 반출 정책이 근거 전송을 허용하지 않아 답변을 생성하지 않았습니다.",
                refused=True,
                refusal_reason="model_egress_denied",
                egress_decision=decision,
            )

        generation_request = self._generation_request(
            request,
            prepared.evidence,
            ontology_bundle.context,
        )
        try:
            result = self._generator.generate(generation_request)
            if (
                result.model.provider != self._generator.provider
                or result.model.model != self._generator.model
                or result.model.revision != self._generator.revision
            ):
                raise ValidationError(
                    "generation result model revision does not match the configured adapter"
                )
            validate_generation_result(
                result,
                allowed_evidence_ids=tuple(
                    item.id for item in generation_request.evidence
                ),
                max_claims=generation_request.max_claims,
            )
            return assemble_generated_answer(
                request,
                prepared.evidence,
                result,
                decision,
                ontology_bundle.context,
            )
        except DependencyUnavailableError:
            if self._fallback_on_error:
                return extractive.model_copy(
                    update={
                        "egress_decision": decision,
                        "warnings": ["generation_unavailable_extractive_fallback"],
                    }
                )
            return self._generation_refusal(
                request,
                "generation_unavailable",
                "구조화 생성기를 사용할 수 없어 답변을 확정하지 않았습니다.",
                decision=decision,
            )
        except ValidationError:
            if self._fallback_on_error:
                return extractive.model_copy(
                    update={
                        "egress_decision": decision,
                        "warnings": ["generation_invalid_extractive_fallback"],
                    }
                )
            return self._generation_refusal(
                request,
                "generation_invalid",
                "생성 결과의 근거 인용을 검증할 수 없어 답변을 확정하지 않았습니다.",
                decision=decision,
            )

    def _generation_request(
        self,
        request: AnswerRequest,
        evidence: tuple[EvidenceRead, ...],
        ontology_context: OntologyAnswerContext | None,
    ) -> GenerationRequest:
        remaining = request.max_chars
        items: list[GenerationEvidence] = []
        fully_included_ids: set[str] = set()
        for item in evidence:
            if remaining <= 0:
                break
            body = item.unit.body[:remaining]
            if not body:
                continue
            items.append(
                GenerationEvidence(
                    id=item.unit.id,
                    body=body,
                    locator=json.dumps(
                        item.unit.locator.model_dump(mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )
            if len(body) == len(item.unit.body):
                fully_included_ids.add(item.unit.id)
            remaining -= len(body)
        return GenerationRequest(
            query=request.query,
            evidence=tuple(items),
            relations=_generation_relations(
                ontology_context,
                fully_included_ids,
            ),
            max_claims=self._max_claims,
            max_output_tokens=self._max_output_tokens,
        )

    @staticmethod
    def _generation_refusal(
        request: AnswerRequest,
        reason: AnswerRefusalReason,
        answer: str,
        *,
        decision: EgressDecision | None = None,
    ) -> AnswerResponse:
        return AnswerResponse(
            query=request.query,
            answer=answer,
            refused=True,
            refusal_reason=reason,
            egress_decision=decision,
        )


def _generation_relations(
    context: OntologyAnswerContext | None,
    evidence_ids: set[str],
) -> tuple[GenerationRelation, ...]:
    if context is None:
        return ()
    return tuple(
        GenerationRelation(
            assertion_id=edge.assertion_id,
            subject_id=edge.subject_id,
            predicate=edge.predicate,
            object_entity_id=edge.object_entity_id,
            object_value=edge.object_value,
            evidence_ids=tuple(edge.evidence_unit_ids),
        )
        for edge in context.edges
        if edge.evidence_unit_ids
        and set(edge.evidence_unit_ids).issubset(evidence_ids)
    )


def _context_is_cited(
    context: OntologyAnswerContext | None,
    citation_ids: set[str],
) -> bool:
    return context is not None and set(context.evidence_unit_ids).issubset(
        citation_ids
    )
