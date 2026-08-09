from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from kip.domain.knowledge import RelationProposal
from kip.domain.models import EvidenceRead


class RelationMinerPort(Protocol):
    name: str
    model: str
    revision: str

    def mine(
        self,
        *,
        evidence: Sequence[EvidenceRead],
        ontology_version: str,
    ) -> list[RelationProposal]: ...
