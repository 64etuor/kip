"""Shared, content-derived text-quality signals for parsers.

A static per-parser quality constant cannot reflect real degradation: a
corrupted XLSX sheet, a PPTX part that failed to parse, or a garbled HWP
extraction all still emit *some* text, and the fixed constant would silently
overstate confidence in it. These helpers give every format-specific parser
(xlsx, pptx, hwp_native, hwp_broker) the same small vocabulary for scoring
extracted text from its content:

- how much of it is intact, printable content (:func:`printable_ratio`);
- how much is recognizable Hangul (:func:`hangul_ratio`), used as a coarse
  signal that Korean text decoded correctly rather than into mojibake;
- how much is the Unicode replacement character (:func:`replacement_ratio`),
  inserted whenever bytes could not be decoded under the assumed encoding.

Every ratio is bounded to ``[0.0, 1.0]`` and computed over Unicode code
points. An empty string always yields ``0.0`` (there is nothing to credit or
penalize).
"""

from __future__ import annotations

_REPLACEMENT_CHARACTER = "�"


def printable_ratio(text: str) -> float:
    """Fraction of characters that are printable (plus newline/tab).

    A low ratio indicates control characters or otherwise non-textual bytes
    leaked into the extracted text.
    """
    if not text:
        return 0.0
    printable = sum(1 for char in text if char.isprintable() or char in "\n\t")
    return printable / len(text)


def hangul_ratio(text: str) -> float:
    """Fraction of characters in the Hangul syllable block (U+AC00-U+D7A3).

    Used as a coarse proxy for "this is intact Korean text" rather than an
    encoding failure that produced mostly non-Hangul filler.
    """
    if not text:
        return 0.0
    hangul = sum(1 for char in text if "가" <= char <= "힣")
    return hangul / len(text)


def replacement_ratio(text: str) -> float:
    """Fraction of characters that are the Unicode replacement character.

    ``U+FFFD`` is what Python (and most decoders) substitute for bytes that
    cannot be decoded under the assumed encoding. A clean extraction has a
    ratio of ``0.0``; any positive ratio is direct evidence of decode loss.
    """
    if not text:
        return 0.0
    return text.count(_REPLACEMENT_CHARACTER) / len(text)


def hwp_text_quality(text: str, *, warning_count: int = 0) -> float:
    """Score an HWP/HWPX text extraction from its content.

    Combines a length-based confidence term (more extracted text is more
    likely a full, successful extraction, saturating at 2000 characters),
    the printable-character ratio, and a Hangul-density term (weighted so a
    document is not penalized further once at least a third of its
    characters are Hangul), minus a small penalty per parser warning.
    Bounded to ``[0.0, 1.0]``; an empty string scores ``0.0``.

    This is the formula the command-line HWP broker
    (:mod:`kip.adapters.parsers.hwp_broker`) originally used; the native HWP
    parser (:mod:`kip.adapters.parsers.hwp_native`) reuses it instead of a
    flat constant so both report comparable, content-derived confidence.
    """
    if not text:
        return 0.0
    length_confidence = min(1.0, len(text) / 2000)
    warning_penalty = min(0.3, warning_count * 0.03)
    return max(
        0.0,
        min(
            1.0,
            0.4 * length_confidence
            + 0.3 * printable_ratio(text)
            + 0.3 * min(1.0, hangul_ratio(text) * 3)
            - warning_penalty,
        ),
    )
