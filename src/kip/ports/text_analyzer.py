from __future__ import annotations

from typing import Protocol


class TextAnalyzerPort(Protocol):
    def analyze(self, text: str) -> str: ...
