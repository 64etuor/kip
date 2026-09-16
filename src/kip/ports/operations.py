from __future__ import annotations

from pathlib import Path
from typing import Protocol

from kip.domain.json_types import JsonObject
from kip.domain.models import MigrationReport, RequestContext, StatusReport


class OperationsStore(Protocol):
    @property
    def name(self) -> str: ...

    def ping(self) -> None:
        """Cheap readiness round-trip against the canonical store.

        Raises on failure; must never trigger a sync, re-index, or rebuild.
        """
        ...

    def extension_versions(self, name: str) -> tuple[str | None, str | None] | None:
        """`(installed, default)`: `pg_extension.extversion` and the server's default version.

        Each side is None on its own: `(installed, None)` for an extension
        installed in the database whose files the server lacks, `(None,
        default)` for one the server ships but the database has not installed,
        `(None, None)` for neither. Returns None when the store has no extension
        catalog (the memory repository). Read-only.
        """
        ...

    def migrate(self, migrations_dir: Path) -> MigrationReport:
        """Apply pending migration files, then bring extension catalogs current.

        The extension step is not recorded in the migration ledger, so it runs
        on every call; what it cannot do is returned as `warnings`.
        """
        ...

    def status(self, context: RequestContext) -> StatusReport: ...

    def rebuild_projection(
        self,
        context: RequestContext,
        projection: str,
    ) -> JsonObject: ...

    def export_canonical(self, context: RequestContext, output: Path) -> JsonObject: ...
