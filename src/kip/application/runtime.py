from __future__ import annotations

from dataclasses import dataclass

from kip.application.egress import EgressPolicyUseCases
from kip.application.evidence import EvidenceUseCases
from kip.application.ingestion import IngestionUseCases
from kip.application.knowledge import KnowledgeUseCases
from kip.application.operations import OperationsUseCases
from kip.application.search import RetrievalUseCases


@dataclass(frozen=True, slots=True)
class Application:
    ingestion: IngestionUseCases
    retrieval: RetrievalUseCases
    evidence: EvidenceUseCases
    knowledge: KnowledgeUseCases
    operations: OperationsUseCases
    egress: EgressPolicyUseCases
