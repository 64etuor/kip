from __future__ import annotations

from dataclasses import dataclass

from kip.application.answering import AnsweringUseCases
from kip.application.egress import EgressPolicyUseCases
from kip.application.evidence import EvidenceUseCases
from kip.application.ingestion import IngestionUseCases
from kip.application.knowledge import KnowledgeUseCases
from kip.application.ontology_context import OntologyContextUseCases
from kip.application.ontology_migrations import OntologyMigrationUseCases
from kip.application.ontology_rag import OntologyRagUseCases
from kip.application.operations import OperationsUseCases
from kip.application.search import RetrievalUseCases
from kip.application.telemetry import TelemetryUseCases


@dataclass(frozen=True, slots=True)
class Application:
    ingestion: IngestionUseCases
    retrieval: RetrievalUseCases
    evidence: EvidenceUseCases
    knowledge: KnowledgeUseCases
    operations: OperationsUseCases
    egress: EgressPolicyUseCases
    answering: AnsweringUseCases
    ontology_rag: OntologyRagUseCases
    ontology_context: OntologyContextUseCases
    ontology_migrations: OntologyMigrationUseCases
    telemetry: TelemetryUseCases
