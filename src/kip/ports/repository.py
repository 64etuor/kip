from __future__ import annotations

from typing import Protocol

from kip.ports.evidence import EvidenceStore
from kip.ports.ingestion import IngestionStore
from kip.ports.jobs import JobStore
from kip.ports.knowledge import KnowledgeStore
from kip.ports.operations import OperationsStore
from kip.ports.retrieval import RetrievalStore


class RepositoryPort(
    IngestionStore,
    RetrievalStore,
    EvidenceStore,
    KnowledgeStore,
    JobStore,
    OperationsStore,
    Protocol,
):
    pass
