from __future__ import annotations

from typing import ClassVar

from kip.adapters.repository.postgres.database import PostgresDatabase
from kip.adapters.repository.postgres.evidence import PostgresEvidenceStore
from kip.adapters.repository.postgres.ingestion import PostgresIngestionStore
from kip.adapters.repository.postgres.interactions import PostgresInteractionStore
from kip.adapters.repository.postgres.jobs import PostgresJobStore
from kip.adapters.repository.postgres.knowledge import PostgresKnowledgeStore
from kip.adapters.repository.postgres.operations import PostgresOperationsStore
from kip.adapters.repository.postgres.retrieval import PostgresRetrievalStore
from kip.adapters.telemetry.postgres import PostgresQueryTraceStore


class PostgresRepository:
    name: ClassVar[str] = "postgresql"

    def __init__(
        self,
        database_url: str,
        *,
        statement_timeout_ms: int = 15000,
    ) -> None:
        self.database = PostgresDatabase(
            database_url,
            statement_timeout_ms=statement_timeout_ms,
        )
        self.ingestion = PostgresIngestionStore(self.database)
        self.retrieval = PostgresRetrievalStore(self.database)
        self.evidence = PostgresEvidenceStore(self.database)
        self.knowledge = PostgresKnowledgeStore(self.database)
        self.jobs = PostgresJobStore(self.database)
        self.operations = PostgresOperationsStore(self.database)
        self.telemetry = PostgresQueryTraceStore(self.database)
        self.interactions = PostgresInteractionStore(self.database)


__all__ = ["PostgresRepository"]
