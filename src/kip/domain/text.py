from __future__ import annotations

import re
import unicodedata

# Presentation-only inline markup emitted by Markdown-producing extractors
# (pdf-inspector marks bold, italic and superscript runs). Removing it keeps
# words that the markup split mid-token, such as ``**시범사업**을``, searchable
# as written.
_INLINE_TAG_RE = re.compile(
    r"</?(?:u|sup|sub|b|i|em|strong|s|del|ins|mark|small)>",
    re.IGNORECASE,
)
# Paired emphasis runs only (``***x***``, ``**x**``, ``*x*``) on one line: a
# lone or unpaired run such as a masked ``홍**동`` or ``010-****-1234`` stays.
# The content may not contain the same run, so a closer pairs with the nearest
# opener as in CommonMark (``시행*에 ... *돌봄*을`` keeps the footnote star).
_EMPHASIS_RES = (
    re.compile(r"(?<!\*)\*\*\*(?=[^\s*])((?:(?!\*\*\*)[^\n])+?)(?<=[^\s*])\*\*\*(?!\*)"),
    re.compile(r"(?<!\*)\*\*(?=[^\s*])((?:(?!\*\*)[^\n])+?)(?<=[^\s*])\*\*(?!\*)"),
    re.compile(r"(?<!\*)\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?!\*)"),
)


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text)
    value = value.replace("\x00", " ")
    return " ".join(value.split())


def strip_inline_markup(text: str) -> str:
    """Drop paired Markdown emphasis and inline HTML presentation tags, keeping their text."""
    value = text
    for _ in range(2):  # one level of nesting, e.g. ``**a *b* c**``
        for pattern in _EMPHASIS_RES:
            value = pattern.sub(r"\1", value)
    return _INLINE_TAG_RE.sub("", value)
