from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kip.evaluation.models import ExpectedAssertion
from kip.evaluation.ontology import (
    OntologyAssertionObservation,
    OntologyContradiction,
    OntologyPathObservation,
    OntologyReview,
    evaluate_ontology,
)


def _expected() -> ExpectedAssertion:
    return ExpectedAssertion(
        subject_id="ent_letter",
        predicate="records_decision",
        object_entity_id="ent_decision",
        evidence_ids=["unit_evidence"],
    )


def _actual(*, assertion_id: str = "assertion_reviewed") -> OntologyAssertionObservation:
    return OntologyAssertionObservation(
        assertion_id=assertion_id,
        subject_id="ent_letter",
        predicate="records_decision",
        object_entity_id="ent_decision",
        evidence_ids=("unit_evidence",),
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_ontology_metrics_cover_reviewed_graph_quality() -> None:
    review = OntologyReview(
        case_id="ONTOLOGY-001",
        expected_entity_ids=("ent_letter", "ent_decision"),
        actual_entity_ids=("ent_letter", "ent_decision"),
        expected_assertions=(_expected(),),
        actual_assertions=(_actual(),),
        expected_paths=(("assertion_reviewed",),),
        actual_paths=(
            OntologyPathObservation(
                node_ids=("ent_letter", "ent_decision"),
                assertion_ids=("assertion_reviewed",),
            ),
        ),
        expected_contradictions=(
            OntologyContradiction(assertion_ids=("assertion_old", "assertion_new")),
        ),
        detected_contradictions=(
            OntologyContradiction(assertion_ids=("assertion_new", "assertion_old")),
        ),
        as_of=datetime(2026, 8, 9, tzinfo=UTC),
    )

    metrics = evaluate_ontology(review)

    assert metrics.entity_precision == 1.0
    assert metrics.entity_recall == 1.0
    assert metrics.relation_precision == 1.0
    assert metrics.relation_recall == 1.0
    assert metrics.evidence_precision == 1.0
    assert metrics.evidence_recall == 1.0
    assert metrics.contradiction_precision == 1.0
    assert metrics.contradiction_recall == 1.0
    assert metrics.path_relevance == 1.0
    assert metrics.path_recall == 1.0
    assert metrics.temporal_accuracy == 1.0
    assert metrics.duplicate_count == 0
    assert metrics.orphan_count == 0
    assert metrics.acl_leakage_count == 0


def test_ontology_metrics_count_duplicates_orphans_temporal_and_acl_failures() -> None:
    expired = _actual(assertion_id="assertion_forbidden").model_copy(
        update={"valid_to": datetime.now(UTC) - timedelta(days=1)}
    )
    orphan = OntologyAssertionObservation(
        assertion_id="assertion_orphan",
        subject_id="ent_missing",
        predicate="records_decision",
        object_entity_id="ent_decision",
        evidence_ids=("unit_forbidden",),
    )
    review = OntologyReview(
        case_id="ONTOLOGY-002",
        expected_entity_ids=("ent_letter", "ent_decision"),
        actual_entity_ids=("ent_letter", "ent_decision", "ent_decision"),
        expected_assertions=(_expected(),),
        actual_assertions=(expired, expired, orphan),
        expected_paths=(("assertion_reviewed",),),
        actual_paths=(
            OntologyPathObservation(
                node_ids=("ent_missing", "ent_decision"),
                assertion_ids=("assertion_orphan",),
            ),
        ),
        as_of=datetime.now(UTC),
        forbidden_assertion_ids=("assertion_forbidden",),
        forbidden_evidence_ids=("unit_forbidden",),
    )

    metrics = evaluate_ontology(review)

    assert metrics.relation_recall == 1.0
    assert metrics.relation_precision == 0.5
    assert metrics.path_relevance == 0.0
    assert metrics.temporal_accuracy == 0.5
    assert metrics.duplicate_count == 2
    assert metrics.orphan_count == 1
    assert metrics.acl_leakage_count == 2


def test_unreviewed_ontology_dimensions_remain_null() -> None:
    metrics = evaluate_ontology(OntologyReview(case_id="ONTOLOGY-003"))

    assert metrics.entity_precision is None
    assert metrics.relation_recall is None
    assert metrics.evidence_recall is None
    assert metrics.contradiction_recall is None
    assert metrics.path_relevance is None
    assert metrics.temporal_accuracy is None
