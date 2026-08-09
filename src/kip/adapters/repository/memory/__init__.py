from __future__ import annotations

from typing import ClassVar

from kip.adapters.repository.memory.evidence import MemoryEvidenceStore
from kip.adapters.repository.memory.ingestion import MemoryIngestionStore
from kip.adapters.repository.memory.jobs import MemoryJobStore
from kip.adapters.repository.memory.knowledge import MemoryKnowledgeStore
from kip.adapters.repository.memory.operations import MemoryOperationsStore
from kip.adapters.repository.memory.retrieval import MemoryRetrievalStore
from kip.adapters.repository.memory.state import MemoryState
from kip.ports.retrieval import RetrievalStore


class MemoryRepository:
    name: ClassVar[str] = "memory"

    def __init__(
        self,
        state: MemoryState | None = None,
        *,
        retrieval: RetrievalStore | None = None,
    ) -> None:
        self.state = state or MemoryState()
        self.ingestion = MemoryIngestionStore(self.state)
        self.retrieval = retrieval or MemoryRetrievalStore(self.state)
        self.evidence = MemoryEvidenceStore(self.state)
        self.knowledge = MemoryKnowledgeStore(self.state)
        self.jobs = MemoryJobStore(self.state)
        self.operations = MemoryOperationsStore(self.state)


__all__ = ["MemoryRepository"]
