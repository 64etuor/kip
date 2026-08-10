from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest

from kip.domain.models import EvidenceLocator, SearchHit
from kip.errors import ValidationError
from kip.evaluation.answers import AnswerReview, ReviewedClaim
from kip.evaluation.models import ExpectedAssertion, GoldenCase, GoldenDataset
from kip.evaluation.ontology import (
    OntologyAssertionObservation,
    OntologyContradiction,
    OntologyPathObservation,
    OntologyReview,
)
from kip.evaluation.reviews import EvaluationReviewBundle, VariantReviews
from kip.evaluation.runner import (
    code_fingerprint,
    compare_variants,
    configuration_fingerprint,
    load_dataset,
    run_evaluation,
    validate_activation_report,
)


def _write_dataset(path: Path, *, duplicate: bool = False) -> None:
    second_id = "GQ-001" if duplicate else "GQ-002"
    path.write_text(
        f"""
schema_version: kip.golden-dataset.v1
name: fixture
corpus_fingerprint: sha256:fixture
cases:
  - id: GQ-001
    question: 참여율 변경 승인
    category: exact_identifier
    principal: principal_public
    acl_scopes: [workspace:default, public]
    expected_documents: [doc_a]
    forbidden_documents: [doc_secret]
    expected_evidence:
      - type: text_span
        data:
          line_start: 1
    recall_at: 10
  - id: {second_id}
    question: 허가되지 않은 내부 자료
    category: access_denied
    principal: principal_public
    acl_scopes: [workspace:default, public]
    expected_documents: []
    forbidden_documents: [doc_secret]
    recall_at: 10
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _hit(document_id: str, unit_id: str = "unit_a") -> SearchHit:
    return SearchHit(
        unit_id=unit_id,
        document_id=document_id,
        artifact_id="art_a",
        source_kind="filesystem",
        title="승인 공문",
        snippet="참여율 변경을 승인한다.",
        score=10.0,
        locator=EvidenceLocator(type="text_span", data={"line_start": 1, "line_end": 2}),
        source_uri="file:///public/approval.txt",
        source_sha256="a" * 64,
    )


def test_load_dataset_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    dataset = tmp_path / "duplicate.yaml"
    _write_dataset(dataset, duplicate=True)

    with pytest.raises(ValueError, match="unique"):
        load_dataset(dataset)


def test_run_evaluation_returns_required_metrics_and_redacted_cases(tmp_path: Path) -> None:
    dataset_path = tmp_path / "golden.yaml"
    _write_dataset(dataset_path)

    def search(case, variant):
        assert variant == "lexical"
        return [_hit("doc_a")] if case.id == "GQ-001" else []

    report = run_evaluation(
        load_dataset(dataset_path),
        variants=["lexical"],
        search=search,
        workspace="default",
        dataset_bytes=dataset_path.read_bytes(),
        configuration={"search": {"semantic_enabled": False}},
        code_root=tmp_path,
        now=lambda: datetime(2026, 7, 30, tzinfo=UTC),
    )

    metrics = report["variants"]["lexical"]["metrics"]
    assert metrics["case_count"] == 2
    assert metrics["recall_at_k"] == 1.0
    assert metrics["unauthorized_result_count"] == 0
    assert metrics["locator_accuracy"] == 1.0
    assert report["fingerprints"]["dataset"].startswith("sha256:")
    assert "참여율 변경을 승인한다" not in str(report)


def test_enrichment_makes_stale_and_latest_metrics_measurable(tmp_path: Path) -> None:
    dataset_path = tmp_path / "golden.yaml"
    dataset_path.write_text(
        """
schema_version: kip.golden-dataset.v1
name: fixture
corpus_fingerprint: sha256:fixture
cases:
  - id: GQ-001
    question: 참여율 변경 승인
    category: stale_source
    principal: principal_public
    acl_scopes: [workspace:default, public]
    expected_documents: [doc_a]
    expected_latest: true
    expected_stale_warning: true
    recall_at: 10
""".strip()
        + "\n",
        encoding="utf-8",
    )

    def search(case, variant):
        hit = _hit("doc_a")
        return [
            hit.model_copy(
                update={"metadata": {**hit.metadata, "is_latest": True}},
                deep=True,
            )
        ]

    def enrich(case, hits):
        return [
            hit.model_copy(
                update={
                    "metadata": {
                        **hit.metadata,
                        "source_changed_since_index": True,
                    }
                },
                deep=True,
            )
            for hit in hits
        ]

    report = run_evaluation(
        load_dataset(dataset_path),
        variants=["lexical"],
        search=search,
        workspace="default",
        dataset_bytes=dataset_path.read_bytes(),
        configuration={"search": {"semantic_enabled": False}},
        code_root=tmp_path,
        now=lambda: datetime(2026, 8, 10, tzinfo=UTC),
        enrich=enrich,
    )

    metrics = report["variants"]["lexical"]["metrics"]
    assert metrics["stale_warning_rate"] == 1.0
    assert metrics["latest_version_accuracy"] == 1.0


def test_run_evaluation_excludes_declared_warmup_passes_from_metrics(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "golden.yaml"
    _write_dataset(dataset_path)
    calls: list[tuple[str, str]] = []

    def search(case, variant):
        calls.append((case.id, variant))
        return [_hit("doc_a")] if case.id == "GQ-001" else []

    report = run_evaluation(
        load_dataset(dataset_path),
        variants=["lexical"],
        search=search,
        workspace="default",
        dataset_bytes=dataset_path.read_bytes(),
        configuration={},
        code_root=tmp_path,
        warmup_passes=1,
        now=lambda: datetime(2026, 7, 30, tzinfo=UTC),
    )

    assert calls == [
        ("GQ-001", "lexical"),
        ("GQ-002", "lexical"),
        ("GQ-001", "lexical"),
        ("GQ-002", "lexical"),
    ]
    assert report["run"]["warmup_passes"] == 1
    assert report["variants"]["lexical"]["metrics"]["case_count"] == 2


def test_compare_variants_applies_activation_gate() -> None:
    report = {
        "run": {
            "dataset_gate_eligible": True,
            "required_dimensions": ["retrieval"],
        },
        "variants": {
            "lexical": {
                "metrics": {
                    "recall_at_k": 0.80,
                    "unauthorized_result_count": 0,
                    "stale_warning_rate": 1.0,
                    "failed_case_count": 0,
                },
                "latency_ms": {"p95": 100.0},
                "categories": {"exact_identifier": {"recall_at_k": 1.0}},
            },
            "reranked": {
                "metrics": {
                    "recall_at_k": 0.85,
                    "unauthorized_result_count": 0,
                    "stale_warning_rate": 1.0,
                    "failed_case_count": 0,
                },
                "latency_ms": {"p95": 500.0},
                "categories": {"exact_identifier": {"recall_at_k": 1.0}},
            },
        },
    }

    result = compare_variants(report, "lexical", "reranked")

    assert result["decision"]["status"] == "promote"
    assert result["gates"]["overall_recall_improvement"]["passed"] is True


def test_run_evaluation_reports_unmeasured_optional_metrics_as_null(tmp_path: Path) -> None:
    # Given a dataset that declares no locator, latest-version, or stale expectations.
    dataset_path = tmp_path / "unmeasured.yaml"
    dataset_path.write_text(
        """
schema_version: kip.golden-dataset.v1
name: unmeasured
cases:
  - id: GQ-NA-001
    question: 승인 문서
    category: exact_fact
    principal: principal_public
    acl_scopes: [workspace:default, public]
    expected_documents: [doc_a]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    # When retrieval succeeds without exercising those optional dimensions.
    report = run_evaluation(
        load_dataset(dataset_path),
        variants=["lexical"],
        search=lambda _case, _variant: [_hit("doc_a")],
        workspace="default",
        dataset_bytes=dataset_path.read_bytes(),
        configuration={},
        code_root=tmp_path,
        now=lambda: datetime(2026, 8, 6, tzinfo=UTC),
    )

    # Then the report is honest about missing evidence instead of synthesizing 100%.
    metrics = report["variants"]["lexical"]["metrics"]
    assert metrics["locator_accuracy"] is None
    assert metrics["latest_version_accuracy"] is None
    assert metrics["stale_warning_rate"] is None


def test_run_evaluation_counts_failed_cases_and_blocks_promotion(tmp_path: Path) -> None:
    # Given two valid cases where the candidate executor fails one case.
    dataset_path = tmp_path / "golden.yaml"
    _write_dataset(dataset_path)
    dataset = load_dataset(dataset_path)

    def search(case, variant):
        if variant == "reranked" and case.id == "GQ-002":
            raise RuntimeError("candidate unavailable")
        return [_hit("doc_a")] if case.id == "GQ-001" else []

    # When both variants are evaluated and compared.
    report = run_evaluation(
        dataset,
        variants=["lexical", "reranked"],
        search=search,
        workspace="default",
        dataset_bytes=dataset_path.read_bytes(),
        configuration={},
        code_root=tmp_path,
        now=lambda: datetime(2026, 8, 6, tzinfo=UTC),
    )
    decision = compare_variants(report, "lexical", "reranked")

    # Then execution failures are counted and are a mandatory failed gate.
    assert report["variants"]["reranked"]["metrics"]["failed_case_count"] == 1
    assert decision["gates"]["failed_cases"]["passed"] is False
    assert decision["decision"]["status"] == "keep_disabled"


def test_compare_variants_does_not_hide_semantic_category_regression() -> None:
    # Given one semantic category improves while another regresses by the same amount.
    report = {
        "run": {
            "dataset_gate_eligible": True,
            "required_dimensions": ["retrieval"],
        },
        "variants": {
            "lexical": {
                "metrics": {
                    "recall_at_k": 0.80,
                    "unauthorized_result_count": 0,
                    "stale_warning_rate": 1.0,
                    "failed_case_count": 0,
                },
                "latency_ms": {"p95": 100.0},
                "categories": {
                    "semantic_easy": {"recall_at_k": 0.70},
                    "semantic_hard": {"recall_at_k": 0.70},
                },
            },
            "reranked": {
                "metrics": {
                    "recall_at_k": 0.80,
                    "unauthorized_result_count": 0,
                    "stale_warning_rate": 1.0,
                    "failed_case_count": 0,
                },
                "latency_ms": {"p95": 500.0},
                "categories": {
                    "semantic_easy": {"recall_at_k": 0.90},
                    "semantic_hard": {"recall_at_k": 0.50},
                },
            },
        },
    }

    # When the activation decision aggregates semantic quality.
    result = compare_variants(report, "lexical", "reranked")

    # Then the regression is not hidden by selecting only the best category delta.
    assert result["deltas"]["semantic_recall_at_k"] == pytest.approx(0.0)
    assert result["gates"]["semantic_regression"]["passed"] is False
    assert result["gates"]["semantic_recall_improvement"]["passed"] is False
    assert result["decision"]["status"] == "keep_disabled"


def test_activation_report_must_promote_current_code_and_configuration(tmp_path: Path) -> None:
    configuration = {"models": {"embedding": {"revision": "fixture"}}}
    report = {
        "schema_version": "kip.evaluation-report.v1",
        "run": {
            "id": "eval_fixture",
            "dataset_gate_eligible": True,
            "required_dimensions": ["retrieval"],
        },
        "fingerprints": {
            "configuration": configuration_fingerprint(configuration),
            "code": code_fingerprint(tmp_path),
        },
        "variants": {
            "lexical": {
                "metrics": {
                    "recall_at_k": 0.80,
                    "unauthorized_result_count": 0,
                    "stale_warning_rate": 1.0,
                    "failed_case_count": 0,
                },
                "latency_ms": {"p95": 100.0},
                "categories": {"semantic_paraphrase": {"recall_at_k": 0.70}},
            },
            "hybrid": {
                "metrics": {
                    "recall_at_k": 0.85,
                    "unauthorized_result_count": 0,
                    "stale_warning_rate": 1.0,
                    "failed_case_count": 0,
                },
                "latency_ms": {"p95": 500.0},
                "categories": {"semantic_paraphrase": {"recall_at_k": 0.82}},
            },
        },
    }

    decision = validate_activation_report(
        report,
        candidate="hybrid",
        configuration=configuration,
        code_root=tmp_path,
    )

    assert decision["decision"]["status"] == "promote"
    with pytest.raises(ValidationError, match="configuration fingerprint"):
        validate_activation_report(
            report,
            candidate="hybrid",
            configuration={"models": {"embedding": {"revision": "changed"}}},
            code_root=tmp_path,
        )


def test_activation_report_rejects_keep_disabled_decision(tmp_path: Path) -> None:
    configuration: dict = {}
    report = {
        "schema_version": "kip.evaluation-report.v1",
        "run": {"id": "eval_fixture"},
        "fingerprints": {
            "configuration": configuration_fingerprint(configuration),
            "code": code_fingerprint(tmp_path),
        },
        "variants": {
            "lexical": {
                "metrics": {
                    "recall_at_k": 1.0,
                    "unauthorized_result_count": 0,
                    "stale_warning_rate": 1.0,
                    "failed_case_count": 0,
                },
                "latency_ms": {"p95": 100.0},
                "categories": {},
            },
            "vector": {
                "metrics": {
                    "recall_at_k": 1.0,
                    "unauthorized_result_count": 0,
                    "stale_warning_rate": 1.0,
                    "failed_case_count": 0,
                },
                "latency_ms": {"p95": 500.0},
                "categories": {},
            },
        },
    }

    with pytest.raises(ValidationError, match="does not promote"):
        validate_activation_report(
            report,
            candidate="vector",
            configuration=configuration,
            code_root=tmp_path,
        )


def test_draft_or_unversioned_dataset_never_promotes() -> None:
    report = {
        "run": {
            "dataset_gate_eligible": False,
            "required_dimensions": ["retrieval"],
        },
        "variants": {
            "lexical": {
                "metrics": {
                    "recall_at_k": 0.5,
                    "unauthorized_result_count": 0,
                    "stale_warning_rate": 1.0,
                    "failed_case_count": 0,
                },
                "latency_ms": {"p95": 100.0},
                "categories": {},
            },
            "hybrid": {
                "metrics": {
                    "recall_at_k": 0.9,
                    "unauthorized_result_count": 0,
                    "stale_warning_rate": 1.0,
                    "failed_case_count": 0,
                },
                "latency_ms": {"p95": 200.0},
                "categories": {},
            },
        },
    }

    decision = compare_variants(report, "lexical", "hybrid")
    assert decision["gates"]["reviewed_dataset"]["passed"] is False
    assert decision["decision"]["status"] == "keep_disabled"


def test_reviewed_answer_and_ontology_metrics_gate_promotion(tmp_path: Path) -> None:
    source_revision = "sha256:" + "a" * 64
    expected_assertions = [
        ExpectedAssertion(
            subject_id="ent_letter",
            predicate="records_decision",
            object_entity_id="ent_approved",
            evidence_ids=["unit_approved"],
        ),
        ExpectedAssertion(
            subject_id="ent_letter",
            predicate="records_decision",
            object_entity_id="ent_rejected",
            evidence_ids=["unit_rejected"],
        ),
    ]
    case = GoldenCase(
        id="ONTOLOGY-001",
        question="What decisions does the letter record?",
        category="exact_ontology_relation",
        principal="principal_public",
        acl_scopes=["workspace:default", "public"],
        expected_documents=["doc_a"],
        forbidden_documents=["doc_secret"],
        expected_evidence=[{"type": "text_span", "data": {"line_start": 1}}],
        expected_latest=True,
        expected_stale_warning=True,
        lifecycle="golden",
        version="2026.08.1",
        reviewer="role:knowledge-owner",
        source_revision=source_revision,
        expected_claims=["claim_decisions"],
        expected_evidence_ids=["unit_approved", "unit_rejected"],
        expected_entity_ids=["ent_letter", "ent_approved", "ent_rejected"],
        expected_assertions=expected_assertions,
        expected_paths=[["assertion_approved"]],
        expected_contradictions=[["assertion_approved", "assertion_rejected"]],
        forbidden_entity_ids=["ent_secret"],
        forbidden_assertions=["assertion_secret"],
        forbidden_evidence_ids=["unit_secret"],
        expected_refusal=False,
    )
    dataset = GoldenDataset(
        name="ontology-starter",
        corpus_fingerprint="sha256:" + "b" * 64,
        lifecycle="golden",
        version="2026.08.1",
        reviewer="role:evaluation-owner",
        source_revision=source_revision,
        required_dimensions=["retrieval", "answer", "ontology"],
        cases=[case],
    )
    actual_assertions = tuple(
        OntologyAssertionObservation(
            assertion_id=assertion_id,
            subject_id=expected.subject_id,
            predicate=expected.predicate,
            object_entity_id=expected.object_entity_id,
            evidence_ids=tuple(expected.evidence_ids),
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for assertion_id, expected in zip(
            ("assertion_approved", "assertion_rejected"),
            expected_assertions,
            strict=True,
        )
    )
    review_bundle = EvaluationReviewBundle(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        dataset_source_revision=source_revision,
        variants={
            "hybrid": VariantReviews(
                answer=(
                    AnswerReview(
                        case_id=case.id,
                        expected_claims=("claim_decisions",),
                        expected_evidence_ids=("unit_approved", "unit_rejected"),
                        claims=(
                            ReviewedClaim(
                                id="claim-1",
                                expected_claim="claim_decisions",
                                supported_by_evidence=True,
                                citation_locator_correct=True,
                                cited_evidence_ids=("unit_approved", "unit_rejected"),
                            ),
                        ),
                        expected_refusal=False,
                        refused=False,
                    ),
                ),
                ontology=(
                    OntologyReview(
                        case_id=case.id,
                        expected_entity_ids=tuple(case.expected_entity_ids),
                        actual_entity_ids=tuple(case.expected_entity_ids),
                        expected_assertions=tuple(expected_assertions),
                        actual_assertions=actual_assertions,
                        expected_paths=(("assertion_approved",),),
                        actual_paths=(
                            OntologyPathObservation(
                                node_ids=("ent_letter", "ent_approved"),
                                assertion_ids=("assertion_approved",),
                            ),
                        ),
                        expected_contradictions=(
                            OntologyContradiction(
                                assertion_ids=(
                                    "assertion_approved",
                                    "assertion_rejected",
                                )
                            ),
                        ),
                        detected_contradictions=(
                            OntologyContradiction(
                                assertion_ids=(
                                    "assertion_rejected",
                                    "assertion_approved",
                                )
                            ),
                        ),
                        as_of=datetime(2026, 8, 9, tzinfo=UTC),
                        forbidden_entity_ids=("ent_secret",),
                        forbidden_assertion_ids=("assertion_secret",),
                        forbidden_evidence_ids=("unit_secret",),
                    ),
                ),
            )
        },
    )

    def search(_case, variant):
        if variant == "lexical":
            return []
        return [
            _hit("doc_a").model_copy(
                update={
                    "metadata": {
                        "is_latest": True,
                        "source_changed_since_index": True,
                    }
                }
            )
        ]

    report = run_evaluation(
        dataset,
        variants=["lexical", "hybrid"],
        search=search,
        workspace="default",
        dataset_bytes=b"reviewed fixture",
        configuration={},
        code_root=tmp_path,
        review_bundle=review_bundle,
        now=lambda: datetime(2026, 8, 9, tzinfo=UTC),
    )
    decision = compare_variants(report, "lexical", "hybrid")
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "evaluation/schemas/evaluation-report.schema.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.validate(report, schema)

    assert report["variants"]["hybrid"]["answer_quality"]["metrics"]["claim_recall"] == 1.0
    assert report["variants"]["hybrid"]["ontology_quality"]["metrics"]["relation_recall"] == 1.0
    assert "role:evaluation-owner" not in str(report)
    assert decision["decision"]["status"] == "promote"

    with pytest.raises(ValueError, match="dataset version"):
        run_evaluation(
            dataset,
            variants=["hybrid"],
            search=search,
            workspace="default",
            dataset_bytes=b"reviewed fixture",
            configuration={},
            code_root=tmp_path,
            review_bundle=review_bundle.model_copy(
                update={"dataset_version": "2026.08.2"}
            ),
        )

    changed_case = case.model_copy(update={"expected_claims": ["claim_changed"]})
    changed_dataset = dataset.model_copy(update={"cases": [changed_case]})
    with pytest.raises(ValueError, match="expectations do not match"):
        run_evaluation(
            changed_dataset,
            variants=["hybrid"],
            search=search,
            workspace="default",
            dataset_bytes=b"changed fixture",
            configuration={},
            code_root=tmp_path,
            review_bundle=review_bundle,
        )

    del report["variants"]["hybrid"]["ontology_quality"]
    blocked = compare_variants(report, "lexical", "hybrid")
    assert blocked["gates"]["ontology_relation_recall"]["passed"] is False
    assert blocked["decision"]["status"] == "keep_disabled"
