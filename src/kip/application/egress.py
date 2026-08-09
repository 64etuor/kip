from __future__ import annotations

from collections.abc import Sequence

from kip.domain.egress import (
    ClassifiedEvidence,
    EgressDecision,
    EgressPolicy,
    evaluate_egress,
)
from kip.domain.models import ContentUnit


class EgressPolicyUseCases:
    def __init__(self, policy: EgressPolicy) -> None:
        self.policy = policy

    def decide(self, evidence: Sequence[ContentUnit]) -> EgressDecision:
        return evaluate_egress(
            self.policy,
            [
                ClassifiedEvidence(
                    id=unit.id,
                    classification=unit.classification,
                )
                for unit in evidence
            ],
        )
