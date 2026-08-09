from __future__ import annotations

from typing import Protocol

from kip.domain.generation import GenerationRequest, GenerationResult


class GenerationPort(Protocol):
    name: str
    provider: str
    model: str
    revision: str

    def generate(self, request: GenerationRequest) -> GenerationResult: ...
