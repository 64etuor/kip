from __future__ import annotations

import re
from dataclasses import dataclass

from kip.application.citations import citation_from_evidence
from kip.domain.models import (
    AnswerRefusalReason,
    AnswerRequest,
    AnswerResponse,
    EvidenceRead,
)

_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_NUMERIC_INTENT = frozenset({"금액", "얼마", "합계", "총액", "수량", "비율", "날짜", "기한"})
_QUESTION_WORDS = frozenset(
    {
        "언제",
        "어디",
        "무엇",
        "누구",
        "어떤",
        "어떻게",
        "알려줘",
        "확인",
        "내야",
        "해야",
        "뭐야",
    }
)
_PARTICLE_SUFFIXES = (
    "으로",
    "에서",
    "까지",
    "에게",
    "보다",
    "처럼",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "로",
    "와",
    "과",
    "도",
)
_APPROVAL_ASSERTION_RE = re.compile(r"승인(?:되었|됐|하였다|했다|한다|함|완료|결정)")
_DECISION_TERM_PREFIXES = ("승인", "변경")
_IDENTIFIER_RE = re.compile(
    r"\b(?=[A-Z0-9._/-]*[A-Z])(?=[A-Z0-9._/-]*\d)"
    r"[A-Z0-9]+(?:[-_/][A-Z0-9.]+)+\b",
    re.IGNORECASE,
)
_VALUE_RE = re.compile(r"\d|[일이삼사오육칠팔구십백천만억]+(?:명|년|개월|주|일|시간|점|원|퍼센트)")
_GENERIC_SCOPE_TERMS = frozenset({"승인", "기준", "상태", "조건", "절차", "규정", "내용"})
_ANSWER_PREDICATE_TERMS = frozenset({"되어", "되는지", "있는지", "있는가"})
_INTERROGATIVE_ENDINGS = ("인가", "있는가", "되는가", "나요", "습니까")


def _bigrams(text: str) -> set[str]:
    compact = "".join(_WORD_RE.findall(text.casefold()))
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def _keywords(text: str) -> set[str]:
    keywords: set[str] = set()
    for raw in _WORD_RE.findall(text.casefold()):
        word = raw
        for suffix in _PARTICLE_SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 2:
                word = word[: -len(suffix)]
                break
        if word not in _QUESTION_WORDS:
            keywords.add(word)
    return keywords


def _is_relevant(query: str, body: str) -> bool:
    keywords = _keywords(query)
    if keywords:
        matches = sum(keyword in body.casefold() for keyword in keywords)
        return matches >= min(2, len(keywords))
    query_bigrams = _bigrams(query)
    return bool(query_bigrams) and len(query_bigrams & _bigrams(body)) / len(query_bigrams) >= 0.5


def _decision_subject_score(query: str, body: str) -> int:
    subject_terms = {
        keyword for keyword in _keywords(query) if not keyword.startswith(_DECISION_TERM_PREFIXES)
    }
    return sum(term in body.casefold() for term in subject_terms)


def _normalized_identifiers(text: str) -> set[str]:
    return {
        "".join(character for character in match.casefold() if character.isalnum())
        for match in _IDENTIFIER_RE.findall(text)
    }


def _contains_requested_identifiers(query: str, evidence: EvidenceRead) -> bool:
    identifiers = _normalized_identifiers(query)
    if not identifiers:
        return True
    searchable = "".join(
        character
        for character in f"{evidence.unit.title or ''} {evidence.unit.body}".casefold()
        if character.isalnum()
    )
    return all(identifier in searchable for identifier in identifiers)


def _has_requested_value(request: AnswerRequest, evidence: EvidenceRead) -> bool:
    has_numeric_intent = any(term in request.query for term in _NUMERIC_INTENT)
    return not has_numeric_intent or _VALUE_RE.search(evidence.unit.body) is not None


def _answer_focus_terms(query: str) -> set[str]:
    if "의" not in query:
        return set()
    focus = query.rsplit("의", maxsplit=1)[1]
    return {
        term
        for term in _keywords(focus) - _GENERIC_SCOPE_TERMS
        if term not in _ANSWER_PREDICATE_TERMS and not term.endswith(_INTERROGATIVE_ENDINGS)
    }


def _contains_answer_focus(request: AnswerRequest, evidence: EvidenceRead) -> bool:
    focus_terms = _answer_focus_terms(request.query)
    if not focus_terms:
        return True
    searchable = f"{evidence.unit.title or ''} {evidence.unit.body}".casefold()
    return all(term in searchable for term in focus_terms)


def _requires_clarification(
    request: AnswerRequest,
    evidence: list[EvidenceRead],
) -> bool:
    keywords = _keywords(request.query)
    subject_terms = keywords - _GENERIC_SCOPE_TERMS
    document_ids = {item.unit.document_id for item in evidence if item.unit.document_id is not None}
    short_topic = not _normalized_identifiers(request.query) and len(keywords) <= 2
    return len(document_ids) > 1 and (not subject_terms or short_topic)


def _refusal(
    request: AnswerRequest,
    reason: AnswerRefusalReason,
    answer: str,
) -> AnswerResponse:
    return AnswerResponse(
        query=request.query,
        answer=answer,
        refused=True,
        refusal_reason=reason,
    )


def _requires_exact_xlsx(request: AnswerRequest, evidence: EvidenceRead) -> bool:
    return evidence.unit.locator.type == "xlsx_sheet" and any(
        term in request.query for term in _NUMERIC_INTENT
    )


@dataclass(frozen=True, slots=True)
class AnswerPreparation:
    evidence: tuple[EvidenceRead, ...]
    refusal: AnswerResponse | None = None


def prepare_answer_evidence(
    request: AnswerRequest,
    evidence: list[EvidenceRead],
    *,
    had_stale_evidence: bool,
    ontology_evidence_ids: set[str] | None = None,
    apply_lexical_gate: bool = True,
) -> AnswerPreparation:
    ontology_ids = ontology_evidence_ids or set()
    if apply_lexical_gate:
        relevant = [
            item
            for item in evidence
            if item.unit.id in ontology_ids or _is_relevant(request.query, item.unit.body)
        ]
    else:
        relevant = list(evidence)
    if any(_requires_exact_xlsx(request, item) for item in relevant):
        return AnswerPreparation(
            evidence=(),
            refusal=_refusal(
                request,
                "exact_xlsx_read_required",
                "원본 워크북 범위를 지정해 xlsx-read로 확인해야 합니다.",
            ),
        )
    identifier_evidence = [
        item for item in relevant if _contains_requested_identifiers(request.query, item)
    ]
    if relevant and not identifier_evidence:
        return AnswerPreparation(
            evidence=(),
            refusal=_refusal(
                request,
                "answer_not_present",
                "요청한 식별자가 현재 근거에 없어 답을 확정할 수 없습니다.",
            ),
        )
    relevant = identifier_evidence
    focus_evidence = [item for item in relevant if _contains_answer_focus(request, item)]
    if relevant and not focus_evidence:
        return AnswerPreparation(
            evidence=(),
            refusal=_refusal(
                request,
                "answer_not_present",
                "현재 근거에는 질문한 대상의 답이 명시되어 있지 않습니다.",
            ),
        )
    relevant = focus_evidence
    value_evidence = [item for item in relevant if _has_requested_value(request, item)]
    if relevant and not value_evidence:
        return AnswerPreparation(
            evidence=(),
            refusal=_refusal(
                request,
                "answer_not_present",
                "현재 근거에는 요청한 수치나 값이 명시되어 있지 않습니다.",
            ),
        )
    relevant = value_evidence
    if _requires_clarification(request, relevant):
        return AnswerPreparation(
            evidence=(),
            refusal=_refusal(
                request,
                "clarification_required",
                "여러 문서가 해당하므로 대상이나 업무 범위를 더 구체적으로 지정해 주세요.",
            ),
        )
    if apply_lexical_gate and "승인" in request.query and relevant:
        subject_scores = [
            _decision_subject_score(request.query, item.unit.body) for item in relevant
        ]
        best_subject_score = max(subject_scores)
        decision_evidence = (
            [
                item
                for item, score in zip(relevant, subject_scores, strict=True)
                if score == best_subject_score
            ]
            if best_subject_score > 0
            else relevant
        )
        if decision_evidence and not any(
            _APPROVAL_ASSERTION_RE.search(item.unit.body) for item in decision_evidence
        ):
            return AnswerPreparation(
                evidence=(),
                refusal=AnswerResponse(
                    query=request.query,
                    answer="현재 근거는 승인 완료를 명시하지 않으므로 승인 여부를 확정할 수 없습니다.",
                    refused=True,
                    refusal_reason="insufficient_decision_evidence",
                    citations=[citation_from_evidence(item) for item in decision_evidence],
                ),
            )
        relevant = decision_evidence
    if not relevant:
        return AnswerPreparation(
            evidence=(),
            refusal=_refusal(
                request,
                "no_fresh_evidence" if had_stale_evidence else "no_admissible_evidence",
                "현재 접근 가능하고 최신인 근거만으로는 답을 확정할 수 없습니다.",
            ),
        )
    return AnswerPreparation(evidence=tuple(relevant))
