from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from kip.adapters.repository.postgres.database import PostgresDatabase
from kip.domain.json_types import JsonObject
from kip.domain.models import RequestContext, StatusReport


@dataclass(frozen=True, slots=True)
class PostgresOperationsStore:
    database: PostgresDatabase
    name: ClassVar[str] = "postgresql"

    def migrate(self, migrations_dir: Path) -> list[str]:
        return self.database.migrate(migrations_dir)

    def status(self, context: RequestContext) -> StatusReport:
        return self.database.status(context)

    def rebuild_projection(
        self,
        context: RequestContext,
        projection: str,
    ) -> JsonObject:
        return self.database.rebuild_projection(context, projection)

    def export_canonical(
        self,
        context: RequestContext,
        output: Path,
    ) -> JsonObject:
        return self.database.export_canonical(context, output)
