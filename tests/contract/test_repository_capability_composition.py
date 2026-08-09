from __future__ import annotations

from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.repository.memory.evidence import MemoryEvidenceStore
from kip.adapters.repository.memory.ingestion import MemoryIngestionStore
from kip.adapters.repository.memory.jobs import MemoryJobStore
from kip.adapters.repository.memory.knowledge import MemoryKnowledgeStore
from kip.adapters.repository.memory.operations import MemoryOperationsStore
from kip.adapters.repository.memory.retrieval import MemoryRetrievalStore
from kip.adapters.repository.postgres import PostgresRepository
from kip.adapters.repository.postgres.evidence import PostgresEvidenceStore
from kip.adapters.repository.postgres.ingestion import PostgresIngestionStore
from kip.adapters.repository.postgres.jobs import PostgresJobStore
from kip.adapters.repository.postgres.knowledge import PostgresKnowledgeStore
from kip.adapters.repository.postgres.operations import PostgresOperationsStore
from kip.adapters.repository.postgres.retrieval import PostgresRetrievalStore


def test_memory_repository_composes_narrow_capability_stores() -> None:
    # Given a memory repository composition root
    repository = MemoryRepository()

    # When its application-facing capabilities are inspected
    capabilities = (
        repository.ingestion,
        repository.retrieval,
        repository.evidence,
        repository.knowledge,
        repository.jobs,
        repository.operations,
    )

    # Then every capability has a focused implementation over one shared state
    assert isinstance(repository.ingestion, MemoryIngestionStore)
    assert isinstance(repository.retrieval, MemoryRetrievalStore)
    assert isinstance(repository.evidence, MemoryEvidenceStore)
    assert isinstance(repository.knowledge, MemoryKnowledgeStore)
    assert isinstance(repository.jobs, MemoryJobStore)
    assert isinstance(repository.operations, MemoryOperationsStore)
    assert {id(capability.state) for capability in capabilities} == {
        id(repository.state)
    }


def test_postgres_repository_composes_narrow_capability_stores() -> None:
    # Given a PostgreSQL repository composition root
    repository = PostgresRepository("postgresql://kip:kip@127.0.0.1:5432/kip")

    # When its application-facing capabilities are inspected
    capabilities = (
        repository.ingestion,
        repository.retrieval,
        repository.evidence,
        repository.knowledge,
        repository.jobs,
        repository.operations,
    )

    # Then every capability has a focused implementation over one database owner
    assert isinstance(repository.ingestion, PostgresIngestionStore)
    assert isinstance(repository.retrieval, PostgresRetrievalStore)
    assert isinstance(repository.evidence, PostgresEvidenceStore)
    assert isinstance(repository.knowledge, PostgresKnowledgeStore)
    assert isinstance(repository.jobs, PostgresJobStore)
    assert isinstance(repository.operations, PostgresOperationsStore)
    assert {id(capability.database) for capability in capabilities} == {
        id(repository.database)
    }
