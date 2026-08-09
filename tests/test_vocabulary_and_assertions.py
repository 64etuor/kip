from __future__ import annotations

import pytest

from kip.domain.models import AssertionCandidate
from kip.errors import ValidationError
from kip.ids import new_id


def test_vocabulary_and_high_risk_assertion_review(test_container):
    path = test_container.settings.project_root / "source" / "공문.txt"
    path.write_text("참여연구원 참여율 변경 승인 공문", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

    terms = test_container.application.retrieval.vocabulary(context, "참여", 20)
    assert any("참여" in item.term for item in terms)

    candidate = AssertionCandidate(
        id=new_id("cand"),
        subject_id="doc_new",
        predicate="amends",
        object_entity_id="doc_old",
        origin="test",
        ontology_version="core/1.0.0",
        evidence=[],
    )
    test_container.application.knowledge.create_candidate(context, candidate)
    with pytest.raises(ValidationError):
        test_container.application.knowledge.review_approve(context, candidate.id)

    unit_id = next(iter(test_container.repository.units))
    candidate.evidence = [{"content_unit_id": unit_id}]
    candidate.status = "proposed"
    test_container.repository.candidates[candidate.id] = candidate
    assertion = test_container.application.knowledge.review_approve(context, candidate.id)
    assert assertion.predicate == "amends"
    assert assertion.evidence_unit_ids == [unit_id]

    explanation = test_container.application.knowledge.explain_assertion(context, assertion.id)
    assert explanation.assertion.id == assertion.id
    assert explanation.source_candidate is not None
    assert explanation.source_candidate.id == candidate.id
    assert explanation.evidence[0].unit.id == unit_id
    assert "참여율 변경 승인" in explanation.evidence[0].unit.body

    edges = test_container.repository.graph_neighbors(
        context,
        __import__("kip.domain.models", fromlist=["GraphNeighborsRequest"]).GraphNeighborsRequest(node_id="doc_new"),
    )
    assert len(edges) == 1
