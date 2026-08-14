from __future__ import annotations

from pathlib import Path
from typing import Protocol

from kip.domain.json_types import JsonObject
from kip.domain.models import RequestContext, StatusReport


class OperationsStore(Protocol):
    @property
    def name(self) -> str: ...

    def ping(self) -> None:
        """Cheap readiness round-trip against the canonical store.

        Raises on failure; must never trigger a sync, re-index, or rebuild.
        """
        ...

    def migrate(self, migrations_dir: Path) -> list[str]: ...

    def status(self, context: RequestContext) -> StatusReport: ...

    def rebuild_projection(
        self,
        context: RequestContext,
        projection: str,
    ) -> JsonObject: ...

    def export_canonical(self, context: RequestContext, output: Path) -> JsonObject: ...
