from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from unicodedata import normalize
from urllib.parse import unquote, urlsplit

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
        # Interrogative endings carry no subject; keeping them as keywords
        # made a one-topic question look off-topic to its own evidence.
        "언제야",
        "언제까지",
        "언제까지야",
        "언제인가",
        "언제인지",
        "어디야",
        "어디인지",
        "누구야",
        "누구인지",
        "무엇인가",
        "무엇인지",
        "얼마야",
        "얼마인지",
        "알려주세요",
        "말해줘",
        "보여줘",
        "보여주세요",
        "열어줘",
        "읽어줘",
        "찾아줘",
        "찾아주세요",
        "요약해줘",
        # Exclusion and generic document words name scope, not a subject:
        # "A.txt 말고 다른 자료의 제출기한" asks about 제출기한.
        "말고",
        "빼고",
        "제외",
        "제외하고",
        "외에",
        "이외",
        "이외에",
        "나머지",
        "다른",
        "자료",
        "문서",
        "파일",
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


def _is_relevant(query: str, text: str) -> bool:
    keywords = _keywords(query)
    if keywords:
        matches = sum(keyword in text.casefold() for keyword in keywords)
        return matches >= min(2, len(keywords))
    query_bigrams = _bigrams(query)
    return bool(query_bigrams) and len(query_bigrams & _bigrams(text)) / len(query_bigrams) >= 0.5


def _relevance_text(evidence: EvidenceRead) -> str:
    # A unit's title (document name, sheet) names its subject as much as the
    # body does; identifier and focus gates already read both.
    return f"{evidence.unit.title or ''} {evidence.unit.body}"


def is_exact_file_reference(query: str, evidence: EvidenceRead) -> bool:
    """A bare filename requests that document's extracts, not a factual claim.

    Do not relax question adequacy just because a filename occurs somewhere
    in a longer question. Match the entire query and only a filesystem URI.
    """
    source = urlsplit(evidence.source_uri)
    if source.scheme != "file" or not evidence.unit.body.strip():
        return False
    filename = PurePosixPath(unquote(source.path)).name
    return bool(filename) and normalize("NFC", query.strip()).casefold() == normalize("NFC", filename).casefold()


_REFERENCE_PUNCTUATION = "?!.,:;)(\"'\u201c\u201d\u2018\u2019"
_REFERENCE_PARTICLES = (
    "에서는", "으로는", "에게는", "에서", "으로", "에게", "까지", "부터", "이랑",
    "의", "은", "는", "이", "가", "을", "를", "에", "로", "와", "과", "도", "랑",
)
# A question that sets a named document aside asks about the others. Each
# entry is a regex fragment; 제외 must close the clause ("제외하고", "제외,
# 다른 ...") because "제외 대상" is a heading inside the document, not scope.
_EXCLUSION_MARKERS = (
    "말고", "빼고", "외에", "이외", "아닌",
    r"제외(?:하고서|하고|하면|하며|하여|한|시키고)",
    r"제외(?=\s*(?:다른|나머지|기타|[,·]|$))",
)
_EXCLUSION_STARTS = ("말고", "빼고", "외에", "이외", "아닌", "제외")


def _file_reference_pattern(filename: str) -> re.Pattern[str]:
    """Match the whole basename at a token boundary, allowing one known particle."""
    escaped = re.escape(normalize("NFC", filename).casefold())
    boundary = re.escape(_REFERENCE_PUNCTUATION)
    particles = "|".join(re.escape(particle) for particle in _REFERENCE_PARTICLES)
    # "A.txt말고" is ordinary spacing; an attached exclusion word still ends the name.
    markers = "|".join(re.escape(marker) for marker in _EXCLUSION_STARTS)
    return re.compile(rf"(?<!\S){escaped}(?:{particles})?(?=$|\s|[{boundary}]|{markers})")


def _file_exclusion_pattern(filename: str) -> re.Pattern[str]:
    """Match the basename immediately followed by an exclusion marker.

    Only "A.txt 말고", "A.txt를 제외하고", "A.txt 외에" set the file aside. A
    marker elsewhere in the question ("정규직이 아닌 인력") is content.
    """
    escaped = re.escape(normalize("NFC", filename).casefold())
    particles = "|".join(re.escape(particle) for particle in _REFERENCE_PARTICLES)
    markers = "|".join(_EXCLUSION_MARKERS)
    return re.compile(rf"(?<!\S){escaped}(?:{particles})?\s*(?:{markers})")


def _evidence_filename(evidence: EvidenceRead) -> str | None:
    source = urlsplit(evidence.source_uri)
    if source.scheme != "file" or not evidence.unit.body.strip():
        return None
    return PurePosixPath(unquote(source.path)).name or None


def _name_key(filename: str) -> str:
    return normalize("NFC", filename).casefold()


def referenced_filename(query: str, evidence: EvidenceRead) -> str | None:
    """The file's basename when the question names it, exactly or embedded.

    Naming a file scopes evidence to it without relaxing adequacy: the rest of
    the question still has to be present in that document. An embedded
    mention needs an extension; a bare common word is not a scope directive.
    """
    filename = _evidence_filename(evidence)
    if filename is None:
        return None
    lowered = normalize("NFC", query).casefold()
    if lowered.strip() == _name_key(filename):
        return filename
    if not PurePosixPath(filename).suffix:
        return None
    return filename if _file_reference_pattern(filename).search(lowered) else None


def excluded_filename(query: str, evidence: EvidenceRead) -> str | None:
    """The file's basename when the question sets this file aside."""
    filename = referenced_filename(query, evidence)
    if filename is None:
        return None
    return filename if _file_exclusion_pattern(filename).search(normalize("NFC", query).casefold()) else None



def _without_file_references(query: str, evidence: list[EvidenceRead]) -> str:
    remainder = normalize("NFC", query).casefold()
    for filename in {name for item in evidence if (name := _evidence_filename(item))}:
        remainder = _file_reference_pattern(filename).sub(" ", remainder)
    return " ".join(remainder.split())


def _without_exclusions(query: str, excluded: list[EvidenceRead]) -> str:
    # Remove the name together with its marker ("A.txt를 제외한"); a leftover
    # inflected marker would otherwise become a required subject keyword.
    remainder = normalize("NFC", query).casefold()
    for filename in {name for item in excluded if (name := _evidence_filename(item))}:
        remainder = _file_exclusion_pattern(filename).sub(" ", remainder)
        remainder = _file_reference_pattern(filename).sub(" ", remainder)
    return " ".join(remainder.split())


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


def _table_evidence(
    evidence: list[EvidenceRead],
) -> tuple[list[EvidenceRead], list[EvidenceRead], list[EvidenceRead]]:
    """Separate complete evidence from workbook discovery and incomplete CSVs.

    Completeness is a property of the source units, not a guess about the
    user's language. CSV chunks qualify together only with exact row coverage
    from the same artifact, extraction, and source revision.
    """
    csv_groups: dict[tuple[str, str, str], list[EvidenceRead]] = {}
    for item in evidence:
        if item.unit.locator.type == "csv_rows" and item.unit.metadata.get("csv_partial_table") is not False:
            key = (item.unit.artifact_id, item.unit.extraction_id, item.indexed_source_sha256)
            csv_groups.setdefault(key, []).append(item)
    complete_csv_ids: set[str] = set()
    for items in csv_groups.values():
        total = items[0].unit.metadata.get("csv_total_row_count")
        if type(total) is not int or total < 1:
            continue
        intervals: list[tuple[int, int]] = []
        for item in {item.unit.id: item for item in items}.values():
            start = item.unit.locator.data.get("start_row")
            end = item.unit.locator.data.get("end_row")
            if (
                item.unit.metadata.get("csv_total_row_count") != total
                or type(start) is not int or type(end) is not int
                or not 2 <= start <= end <= total + 1
            ):
                break
            intervals.append((start, end))
        else:
            cursor = 2  # Row 1 is the header, repeated in each chunk body.
            for start, end in sorted(intervals):
                if start != cursor:
                    break
                cursor = end + 1
            else:
                if cursor == total + 2:
                    complete_csv_ids.update(item.unit.id for item in items)
    usable, shallow, partial = [], [], []
    for item in evidence:
        if item.unit.locator.type == "xlsx_sheet":
            shallow.append(item)
        elif (
            item.unit.locator.type == "csv_rows"
            and item.unit.metadata.get("csv_partial_table") is not False
            and item.unit.id not in complete_csv_ids
        ):
            partial.append(item)
        else:
            usable.append(item)
    return usable, shallow, partial


def _table_refusal(
    request: AnswerRequest, shallow: list[EvidenceRead], partial: list[EvidenceRead],
) -> AnswerPreparation | None:
    if shallow:
        reason: AnswerRefusalReason = "exact_xlsx_read_required"
        message = "원본 워크북 범위를 지정해 xlsx-read로 확인해야 합니다."
        discovery = shallow
    elif partial:
        reason = "csv_full_table_required"
        message = "CSV 전체 행을 포함한 정확한 근거가 필요합니다. 일부 조각만으로 답을 확정할 수 없습니다."
        discovery = partial
    else:
        return None
    response = _refusal(request, reason, message)
    response.citations = [citation_from_evidence(item) for item in discovery]
    return AnswerPreparation(evidence=(), refusal=response)


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
    gate_request = request
    reference_ids = {item.unit.id for item in evidence if is_exact_file_reference(request.query, item)}
    mentioned: dict[str, str] = {}
    if not reference_ids:
        # "A.txt 말고 ..." asks about the other documents: drop the named
        # file from the evidence and gate on the rest of the question.
        excluded = [
            item for item in evidence
            if item.unit.id not in ontology_ids and excluded_filename(request.query, item) is not None
        ]
        if excluded:
            excluded_ids = {item.unit.id for item in excluded}
            evidence = [item for item in evidence if item.unit.id not in excluded_ids]
            gate_request = request.model_copy(update={"query": _without_exclusions(request.query, excluded)})
        mentioned = {
            item.unit.id: name
            for item in evidence
            if item.unit.id not in ontology_ids
            and (name := referenced_filename(gate_request.query, item)) is not None
        }
    if reference_ids or mentioned:
        # Ambiguity is per name: two different files named together are a
        # comparison, not a duplicate. Approved ontology evidence stays.
        contents_by_name: dict[str, set[str]] = {}
        for item in evidence:
            name = _evidence_filename(item) if item.unit.id in reference_ids else mentioned.get(item.unit.id)
            if name is not None:
                contents_by_name.setdefault(_name_key(name), set()).add(item.indexed_source_sha256)
        if any(len(contents) > 1 for contents in contents_by_name.values()):
            return AnswerPreparation(
                evidence=(),
                refusal=_refusal(request, "clarification_required", "같은 이름의 문서가 여러 개입니다. 원본 위치를 확인하고 대상 문서를 지정해 주세요."),
            )
        named_ids = (reference_ids or set(mentioned)) | ontology_ids
        relevant = [item for item in evidence if item.unit.id in named_ids]
    if mentioned:
        remainder = _without_file_references(gate_request.query, relevant)
        if not _keywords(remainder):
            # "계약서.pdf?" or "계약서.pdf 보여줘" asks for the document itself.
            reference_ids = set(mentioned)
        else:
            # The named document is the scope; the rest of the question is
            # what must be present in it. Its own name is not an identifier.
            gate_request = request.model_copy(update={"query": remainder})
            if apply_lexical_gate:
                relevant = [
                    item for item in relevant
                    if item.unit.id in ontology_ids or _is_relevant(remainder, _relevance_text(item))
                ]
                if not relevant:
                    return AnswerPreparation(
                        evidence=(),
                        refusal=_refusal(
                            request,
                            "answer_not_present",
                            "지정한 문서는 찾았지만 요청한 내용이 그 문서의 현재 근거에 없습니다.",
                        ),
                    )
    elif not reference_ids and apply_lexical_gate:
        relevant = [
            item
            for item in evidence
            if item.unit.id in ontology_ids or _is_relevant(gate_request.query, _relevance_text(item))
        ]
    elif not reference_ids:
        relevant = list(evidence)
    relevant, shallow, partial = _table_evidence(relevant)
    if not relevant:
        table_refusal = _table_refusal(request, shallow, partial)
        if table_refusal is not None:
            return table_refusal
    identifier_evidence = [
        item for item in relevant if item.unit.id in reference_ids or item.unit.id in ontology_ids or _contains_requested_identifiers(gate_request.query, item)
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
    focus_evidence = [item for item in relevant if item.unit.id in reference_ids or item.unit.id in ontology_ids or _contains_answer_focus(gate_request, item)]
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
    value_evidence = [item for item in relevant if item.unit.id in reference_ids or item.unit.id in ontology_ids or _has_requested_value(gate_request, item)]
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
    # Focus/value filters may remove a CSV chunk; never retain a now-partial
    # group merely because it was complete before those filters.
    relevant, shallow, partial = _table_evidence(relevant)
    if not relevant:
        table_refusal = _table_refusal(request, shallow, partial)
        if table_refusal is not None:
            return table_refusal
    if not reference_ids and not mentioned and _requires_clarification(gate_request, relevant):
        return AnswerPreparation(
            evidence=(),
            refusal=_refusal(
                request,
                "clarification_required",
                "여러 문서가 해당하므로 대상이나 업무 범위를 더 구체적으로 지정해 주세요.",
            ),
        )
    if apply_lexical_gate and not reference_ids and "승인" in gate_request.query and relevant:
        subject_scores = [
            _decision_subject_score(gate_request.query, item.unit.body) for item in relevant
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
    remaining = request.max_chars
    for item in relevant:
        if item.unit.locator.type == "csv_rows" and len(item.unit.body) > remaining:
            response = _refusal(
                request, "csv_full_table_required",
                "CSV 전체 근거가 max_chars에 들어가지 않습니다. 범위를 좁히거나 문맥 한도를 높여 다시 확인하세요.",
            )
            response.citations = [citation_from_evidence(value) for value in relevant if value.unit.locator.type == "csv_rows"]
            return AnswerPreparation(evidence=(), refusal=response)
        remaining = max(0, remaining - len(item.unit.body))
    return AnswerPreparation(evidence=tuple(relevant))
