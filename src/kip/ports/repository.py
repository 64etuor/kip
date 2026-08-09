from __future__ import annotations

from typing import Protocol

from kip.ports.evidence import EvidenceStore
from kip.ports.ingestion import IngestionStore
from kip.ports.jobs import JobStore
from kip.ports.knowledge import KnowledgeStore
from kip.ports.operations import OperationsStore
from kip.ports.retrieval import RetrievalStore
from kip.ports.telemetry import QueryTraceStore


class RepositoryPort(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def ingestion(self) -> IngestionStore: ...

    @property
    def retrieval(self) -> RetrievalStore: ...

    @property
    def evidence(self) -> EvidenceStore: ...

    @property
    def knowledge(self) -> KnowledgeStore: ...

    @property
    def jobs(self) -> JobStore: ...

    @property
    def operations(self) -> OperationsStore: ...

    @property
    def telemetry(self) -> QueryTraceStore: ...
