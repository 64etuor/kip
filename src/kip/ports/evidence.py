from __future__ import annotations

from pathlib import Path
from typing import Protocol

from kip.domain.json_types import JsonObject
from kip.domain.models import ArtifactView, ContentUnit, EvidenceRead, RequestContext


class EvidenceStore(Protocol):
    def get_content_unit(self, context: RequestContext, unit_id: str) -> ContentUnit: ...

    def get_artifact(self, context: RequestContext, artifact_id: str) -> ArtifactView: ...

    def get_document(self, context: RequestContext, document_id: str) -> JsonObject: ...


class SourceFileInspectorPort(Protocol):
    def sha256(self, path: Path) -> str | None: ...

    def require_sha256(self, path: Path) -> str: ...


class WorkbookReaderPort(Protocol):
    def read(self, path: Path, sheet: str, cell_range: str) -> list[list[JsonObject]]: ...


class EvidenceReaderPort(Protocol):
    def read_unit(self, context: RequestContext, unit_id: str) -> EvidenceRead: ...
