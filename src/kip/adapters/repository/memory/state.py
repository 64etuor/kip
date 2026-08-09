from __future__ import annotations

from kip.domain.json_types import JsonObject
from kip.domain.models import (
    ApprovedAssertion,
    ArtifactView,
    AssertionCandidate,
    ContentUnit,
    DocumentPacket,
    EmbeddingRecord,
    EmbeddingSpace,
    JobRecord,
)


class MemoryState:
    def __init__(self) -> None:
        self.packets_by_revision: dict[str, DocumentPacket] = {}
        self.current_revision_by_object: dict[str, str] = {}
        self.units: dict[str, ContentUnit] = {}
        self.artifacts: dict[str, ArtifactView] = {}
        self.documents: dict[str, JsonObject] = {}
        self.candidates: dict[str, AssertionCandidate] = {}
        self.assertions: dict[str, ApprovedAssertion] = {}
        self.jobs: dict[str, JobRecord] = {}
        self.job_order: list[str] = []
        self.job_errors: dict[str, str] = {}
        self.embedding_spaces: dict[str, EmbeddingSpace] = {}
        self.embeddings: dict[tuple[str, str], EmbeddingRecord] = {}
