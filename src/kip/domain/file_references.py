"""Filename scope derived from a question, before ranking or live reads."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from unicodedata import normalize

from pydantic import Field

from kip.domain.models import SearchRequest

PARTICLES = (
    "에서는", "으로는", "에게는", "에서", "으로", "에게", "까지", "부터", "이랑",
    "의", "은", "는", "이", "가", "을", "를", "에", "로", "와", "과", "도", "랑",
)
_PARTICLES = "|".join(PARTICLES)
_EXCLUSIONS = re.compile(
    r"\s*(?:(?:말고|빼고|외에|이외(?:에)?|아닌|제외(?:하고서|하고|하면|하며|하여|한|시키고))"
    r"(?=$|\s|[?!.,:;·])|제외(?=\s*(?:다른|나머지|기타|[,·]|$)))"
)
_QUOTES = (("\"", "\""), ("'", "'"), ("`", "`"), ("\u201c", "\u201d"), ("\u2018", "\u2019"))
_QUOTED = re.compile(r'"[^"\n]+"|\x27[^\x27\n]+\x27|`[^`\n]+`|\u201c[^\u201d\n]+\u201d|\u2018[^\u2019\n]+\u2019')
DEFAULT_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({
    "txt", "md", "pdf", "hwp", "hwpx", "doc", "docx", "ppt", "pptx", "xls", "xlsx",
    "xlsm", "csv", "tsv", "rtf", "eml", "msg", "json", "html", "htm",
})
_URL = re.compile(r"(?:\S+://|www\.)\S+", re.IGNORECASE)
# Parentheses and brackets are ordinary Korean office naming ("회의록(최종).txt",
# "[공지] 안내.txt"), so they belong inside a basename token and may follow it.
_TOKEN_BOUNDARY = r"(?<![^\s/\\\"'`\u201c\u201d\u2018\u2019(\[])"
_TOKEN_TAIL = (
    rf"(?=$|\s|[?!,:;()\[\]\"'`\u201d\u2019]|\.(?=$|\s)|(?:{_PARTICLES})(?=$|\s|[?!.,:;()\[\]])|말고|빼고|제외|외에|이외)"
)
_ANY_EXTENSION_TOKEN = re.compile(
    _TOKEN_BOUNDARY + r"[^\s/\\\"'`\u201c\u201d\u2018\u2019]+\.[a-z0-9]{1,10}" + _TOKEN_TAIL, re.IGNORECASE,
)
_SPELLING_STOP = re.compile(r"[,;/|]")


def normalized_extensions(extensions: Iterable[str] | None) -> frozenset[str]:
    """Extension names without the dot, lowercased, plus the built-in defaults.

    Deployments index operator-chosen extensions; a question naming a file
    with one of them must fail closed exactly like the built-in list.
    """
    cleaned = {
        value.strip().lstrip(".").casefold()
        for value in (extensions or ())
        if value and value.strip().lstrip(".")
    }
    return DEFAULT_DOCUMENT_EXTENSIONS | frozenset(cleaned)


def _file_token_pattern(extensions: Iterable[str] | None = None) -> re.Pattern[str]:
    alternatives = "|".join(sorted((re.escape(ext) for ext in normalized_extensions(extensions)), key=len, reverse=True))
    return re.compile(
        _TOKEN_BOUNDARY
        + r"[^\s/\\\"'`\u201c\u201d\u2018\u2019]+\.(?:" + alternatives + r")(?:\.[\w-]+)*"
        + _TOKEN_TAIL,
        re.IGNORECASE,
    )


_FILE_TOKEN = _file_token_pattern()


def _url_spans(text: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in _URL.finditer(text)]


def _inside(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(left <= start and end <= right for left, right in spans)


def filename_key(value: str) -> str:
    return normalize("NFC", value).casefold()


@dataclass(frozen=True)
class FileReference:
    name: str
    start: int
    end: int
    excluded: bool = False


def file_references(query: str, filenames: list[str]) -> tuple[FileReference, ...]:
    text = filename_key(query)
    quoted = [(match.start(), match.end()) for match in _QUOTED.finditer(text)]
    candidates = []
    for name in sorted(set(filenames), key=lambda value: (-len(value), value)):
        if "." not in name and text.strip() != filename_key(name):
            continue
        escaped = re.escape(filename_key(name))
        forms = "|".join(re.escape(left) + escaped + re.escape(right) for left, right in _QUOTES)
        pattern = re.compile(
            rf"(?<![^\s(\[])(?:{forms}|{escaped})(?:{_PARTICLES})?"
            r"(?=$|\s|[?!,:;()\[\]\"'`\u201d\u2019]|\.(?=$|\s)|말고|빼고|제외|외에|이외|아닌)"
        )
        for match in pattern.finditer(text):
            # A shorter basename inside a quoted longer name is not a match.
            if any(start < match.start() < end for start, end in quoted):
                continue
            exclusion = _EXCLUSIONS.match(text, match.end())
            candidates.append(FileReference(
                name, match.start(), exclusion.end() if exclusion else match.end(), bool(exclusion),
            ))
    selected: list[FileReference] = []
    for ref in sorted(candidates, key=lambda item: (-(item.end - item.start), item.start)):
        if not any(ref.start < other.end and other.start < ref.end for other in selected):
            selected.append(ref)
    return tuple(sorted(selected, key=lambda item: item.start))


def unresolved_file_tokens(
    query: str, references: tuple[FileReference, ...], extensions: Iterable[str] | None = None,
) -> list[str]:
    """Document-looking names in the question that no allowed file matched.

    URLs are context, not file requests: a pasted link ending in .pdf must
    not be reported as an inaccessible file.
    """
    text = filename_key(query)
    urls = _url_spans(text)
    pattern = _FILE_TOKEN if extensions is None else _file_token_pattern(extensions)
    tokens: list[str] = []
    for match in pattern.finditer(text):
        if _inside(urls, match.start(), match.end()):
            continue
        if any(ref.start <= match.start() and match.end() <= ref.end for ref in references):
            continue
        tokens.append(match.group(0))
    return list(dict.fromkeys(tokens))


def has_unresolved_file_reference(
    query: str, references: tuple[FileReference, ...], extensions: Iterable[str] | None = None,
) -> bool:
    return bool(unresolved_file_tokens(query, references, extensions))


MAX_SPELLING_WORDS = 10


def candidate_basenames(query: str, max_words: int = MAX_SPELLING_WORDS) -> list[str]:
    """Basename spellings a question could contain, for an exact index lookup.

    Quoted spans, extension-bearing tokens and up to `max_words` preceding
    words (stopping at `,;/|` punctuation) joined by single spaces cover
    unquoted names such as `2025 상반기 영업 실적 요약.txt`. The lookup stays
    an equality on the casefolded basename, so the repository can use an
    index; repositories fall back to containment when no spelling matches a
    name that still looks like a file (see `looks_like_file_request`).
    """
    text = filename_key(query)
    urls = _url_spans(text)
    names: list[str] = []
    for match in _QUOTED.finditer(text):
        inner = match.group(0)[1:-1].strip()
        if inner:
            names.append(inner)
    for match in _ANY_EXTENSION_TOKEN.finditer(text):
        if _inside(urls, match.start(), match.end()):
            continue
        token = match.group(0)
        head = _SPELLING_STOP.split(text[: match.start()])[-1]
        preceding = [word.strip("\"'`\u201c\u201d\u2018\u2019") for word in head.split()]
        preceding = [word for word in preceding if word]
        names.append(token)
        for count in range(1, max_words):
            if len(preceding) < count:
                break
            names.append(" ".join([*preceding[-count:], token]))
    return list(dict.fromkeys(name for name in names if name))


_LETTER_EXTENSION = re.compile(r"\.[a-z][a-z0-9]{0,9}$", re.IGNORECASE)


def looks_like_file_request(query: str) -> bool:
    """Whether the question contains a file-looking token outside a URL.

    A version or decimal (`3.8.1`, `3.5`) is not a file: the suffix after the
    final dot must start with a letter. This gates the containment fallback.
    """
    text = filename_key(query)
    urls = _url_spans(text)
    return any(
        not _inside(urls, match.start(), match.end()) and _LETTER_EXTENSION.search(match.group(0)) is not None
        for match in _ANY_EXTENSION_TOKEN.finditer(text)
    )


def without_references(query: str, references: tuple[FileReference, ...], *, excluded_only: bool = False) -> str:
    text = filename_key(query)
    for ref in reversed(references):
        if not excluded_only or ref.excluded:
            text = text[:ref.start] + " " + text[ref.end:]
    return " ".join(text.split())


class FilenameSearchRequest(SearchRequest):
    """Internal criteria only; public CLI/REST/MCP request schemas stay stable."""

    included_filenames: list[str] = Field(default_factory=list)
    excluded_filenames: list[str] = Field(default_factory=list)

    def allows(self, name: str, source_kind: str) -> bool:
        key = filename_key(name)
        included = {filename_key(item) for item in self.included_filenames}
        excluded = {filename_key(item) for item in self.excluded_filenames}
        if included and (source_kind != "filesystem" or key not in included):
            return False
        return source_kind != "filesystem" or key not in excluded
