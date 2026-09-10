from __future__ import annotations

import re
from unicodedata import normalize

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_QUERY_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def discovery_snippet(body: str, query: str, width: int = 360) -> str:
    """Preview the best matching paragraph without changing evidence or ranking.

    Paragraph relevance counts distinct query terms, with source order breaking
    ties. This is a discovery excerpt, not an instruction filter or exact quote:
    Unicode and intra-paragraph whitespace are normalized for presentation.
    """
    if width <= 0:
        raise ValueError("snippet width must be positive")
    normalized = normalize("NFC", body).replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [
        " ".join(part.split())
        for part in _PARAGRAPH_BREAK.split(normalized)
        if part.strip()
    ]
    if not paragraphs:
        return ""
    # Distinct terms are capped so paragraph scoring stays linear in the body.
    terms = tuple(dict.fromkeys(_QUERY_TOKEN.findall(normalize("NFC", query).lower())))[:64]
    paragraph = max(paragraphs, key=lambda part: _matches(part, terms))
    if len(paragraph) <= width:
        return paragraph
    lower = paragraph.lower()
    if len(lower) != len(paragraph):
        # A length-changing case mapping would misalign window offsets.
        lower = paragraph
    starts = {0}
    # Sampling the first and last occurrence of each term bounds window
    # scoring independently of repetition count in a large source unit.
    for term in terms:
        for position in (lower.find(term), lower.rfind(term)):
            if position < 0:
                continue
            starts.add(max(0, min(position - width // 3, len(paragraph) - width)))
    start = 0
    best = -1
    for offset in sorted(starts):
        score = _matches(paragraph[offset:offset + width], terms)
        if score > best:
            start, best = offset, score
        if score == len(terms):
            break
    end = start + width
    return ("…" if start else "") + paragraph[start:end] + ("…" if end < len(paragraph) else "")


def _matches(text: str, terms: tuple[str, ...]) -> int:
    lower = text.lower()
    return sum(term in lower for term in terms)
