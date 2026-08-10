from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from kip.domain.text import normalize_text

_TOKEN_RE: Final = re.compile(r"[0-9A-Za-z가-힣_./:@+-]+")
_HANGUL_SEQUENCE_RE: Final = re.compile(r"[가-힣]+")


@dataclass(frozen=True, slots=True)
class KoreanNgramAnalyzer:
    min_n: int = 2
    max_n: int = 4

    def analyze(self, text: str) -> str:
        normalized = normalize_text(text).lower()
        tokens: list[str] = []
        seen: set[str] = set()
        for match in _TOKEN_RE.finditer(normalized):
            token = match.group(0)
            self._add(tokens, seen, token)
            for hangul_match in _HANGUL_SEQUENCE_RE.finditer(token):
                hangul = hangul_match.group(0)
                self._add(tokens, seen, hangul)
                upper = min(self.max_n, len(hangul))
                for size in range(self.min_n, upper + 1):
                    for start in range(0, len(hangul) - size + 1):
                        self._add(tokens, seen, hangul[start : start + size])
        return " ".join(tokens)

    @staticmethod
    def _add(tokens: list[str], seen: set[str], value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            tokens.append(value)
