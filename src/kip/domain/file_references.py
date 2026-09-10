"""Filename scope derived from a question, before ranking or live reads."""
from __future__ import annotations

import re
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
_FILE_TOKEN = re.compile(
    r"(?<![^\s/\\\"'`\u201c\u201d\u2018\u2019()])"
    r"[^\s/\\\"'`\u201c\u201d\u2018\u2019()]+\.(?:txt|md|pdf|hwp|hwpx|doc|docx|ppt|pptx|xls|xlsx|xlsm|csv|tsv|rtf|eml|msg|json|html|htm)(?:\.[\w-]+)*"
    rf"(?=$|\s|[?!,:;)\"'`\u201d\u2019]|\.(?=$|\s)|(?:{_PARTICLES})(?=$|\s|[?!.,:;])|말고|빼고|제외|외에|이외)",
    re.IGNORECASE,
)


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
            rf"(?<![^\s(])(?:{forms}|{escaped})(?:{_PARTICLES})?"
            r"(?=$|\s|[?!,:;)\"'`\u201d\u2019]|\.(?=$|\s)|말고|빼고|제외|외에|이외|아닌)"
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


def has_unresolved_file_reference(query: str, references: tuple[FileReference, ...]) -> bool:
    return any(
        not any(ref.start <= match.start() and match.end() <= ref.end for ref in references)
        for match in _FILE_TOKEN.finditer(filename_key(query))
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
