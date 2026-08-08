from __future__ import annotations

import re

from kip.domain.models import AnswerCitation, AnswerRequest, AnswerResponse, EvidenceRead

_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_NUMERIC_INTENT = frozenset({"금액", "얼마", "합계", "총액", "수량", "비율", "날짜", "기한"})
_QUESTION_WORDS = frozenset(
    {"언제", "어디", "무엇", "누구", "어떤", "어떻게", "알려줘", "확인", "내야", "해야"}
)
_PARTICLE_SUFFIXES = ("으로", "에서", "까지", "에게", "보다", "처럼", "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도")
_APPROVAL_ASSERTION_RE = re.compile(r"승인(?:되었|됐|하였다|했다|한다|함|완료|결정)")
_DECISION_TERM_PREFIXES = ("승인", "변경")


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
        keyword
        for keyword in _keywords(query)
        if not keyword.startswith(_DECISION_TERM_PREFIXES)
    }
    return sum(term in body.casefold() for term in subject_terms)


def _requires_exact_xlsx(request: AnswerRequest, evidence: EvidenceRead) -> bool:
    return (
        evidence.unit.locator.type == "xlsx_sheet"
        and any(term in request.query for term in _NUMERIC_INTENT)
    )


def _citation(item: EvidenceRead) -> AnswerCitation:
    return AnswerCitation(
        unit_id=item.unit.id,
        artifact_id=item.unit.artifact_id,
        source_uri=item.source_uri,
        locator=item.unit.locator,
        indexed_source_sha256=item.indexed_source_sha256,
        current_source_sha256=item.current_source_sha256,
        source_changed_since_index=False,
    )


def assemble_answer(
    request: AnswerRequest,
    evidence: list[EvidenceRead],
    *,
    had_stale_evidence: bool,
) -> AnswerResponse:
    relevant = [item for item in evidence if _is_relevant(request.query, item.unit.body)]
    if any(_requires_exact_xlsx(request, item) for item in relevant):
        return AnswerResponse(
            query=request.query,
            answer="원본 워크북 범위를 지정해 xlsx-read로 확인해야 합니다.",
            refused=True,
            refusal_reason="exact_xlsx_read_required",
        )
    if "승인" in request.query and relevant:
        subject_scores = [
            _decision_subject_score(request.query, item.unit.body)
            for item in relevant
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
            return AnswerResponse(
                query=request.query,
                answer="현재 근거는 승인 완료를 명시하지 않으므로 승인 여부를 확정할 수 없습니다.",
                refused=True,
                refusal_reason="insufficient_decision_evidence",
                citations=[_citation(item) for item in decision_evidence],
            )
        relevant = decision_evidence
    if not relevant:
        if had_stale_evidence:
            return AnswerResponse(
                query=request.query,
                answer="현재 접근 가능하고 최신인 근거만으로는 답을 확정할 수 없습니다.",
                refused=True,
                refusal_reason="no_fresh_evidence",
            )
        return AnswerResponse(
            query=request.query,
            answer="현재 접근 가능하고 최신인 근거만으로는 답을 확정할 수 없습니다.",
            refused=True,
            refusal_reason="no_admissible_evidence",
        )

    remaining = request.max_chars
    passages: list[str] = []
    citations: list[AnswerCitation] = []
    for item in relevant:
        passage = item.unit.body[:remaining]
        if not passage:
            break
        passages.append(passage)
        citations.append(_citation(item))
        remaining -= len(passage)
        if remaining <= 0:
            break
    return AnswerResponse(
        query=request.query,
        answer="\n\n".join(passages),
        refused=False,
        citations=citations,
    )
