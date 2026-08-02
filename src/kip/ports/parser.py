from __future__ import annotations

from pathlib import Path
from typing import Protocol

from kip.domain.models import ContentUnit, ExtractionRun


class ParseOutput(Protocol):
    extraction: ExtractionRun
    units: list[ContentUnit]


class ParserPort(Protocol):
    name: str
    version: str

    def supports(self, path: Path) -> bool: ...
    def parse(self, path: Path, *, artifact_id: str, document_id: str, acl_scopes: list[str]) -> tuple[ExtractionRun, list[ContentUnit]]: ...
