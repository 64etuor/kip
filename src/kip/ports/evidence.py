from __future__ import annotations

from pathlib import Path
from typing import Protocol

from kip.domain.json_types import JsonObject
from kip.domain.models import ArtifactView, ContentUnit, EvidenceRead, RequestContext
from kip.domain.xlsx import XlsxCell


class EvidenceStore(Protocol):
    def get_content_unit(self, context: RequestContext, unit_id: str) -> ContentUnit: ...

    def get_artifact(self, context: RequestContext, artifact_id: str) -> ArtifactView: ...

    def get_document(self, context: RequestContext, document_id: str) -> JsonObject: ...


class SourceFileInspectorPort(Protocol):
    def sha256(self, path: Path) -> str | None: ...

    def require_sha256(self, path: Path) -> str: ...

    def stat(self, path: Path) -> tuple[int, int] | None:
        """Return (size, mtime_ns) for a readable file, else None."""
        ...


class WorkbookReaderPort(Protocol):
    def read(self, path: Path, sheet: str, cell_range: str) -> list[list[XlsxCell]]: ...

    def supports(self, path: Path) -> bool: ...


class EvidenceReaderPort(Protocol):
    def read_unit(
        self,
        context: RequestContext,
        unit_id: str,
        *,
        verify_hash: bool = True,
    ) -> EvidenceRead: ...
