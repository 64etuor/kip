from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from kip.errors import ValidationError
from kip.quality import (
    QualityExperiment,
    QualityReport,
    load_experiment,
    load_quality_report,
    recommend,
)

ROOT = Path(__file__).resolve().parents[1]


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "kip.quality-experiment.v1",
        "id": "exp_qwen3_reranker",
        "dataset": "evaluation/golden/real-corpus.yaml",
        "baseline_variant": "hybrid",
        "candidate_variant": "reranked",
        "components": [
            {
                "kind": "reranker",
                "adapter": "huggingface",
                "identifier": "Qwen/Qwen3-Reranker-0.6B",
                "revision": "f3f3c8a",
                "configuration": {"max_length": 1024},
            }
        ],
        "fingerprints": {
            "corpus": "sha256:corpus",
            "dataset": "sha256:dataset",
            "configuration": "sha256:configuration",
            "code": "sha256:code",
        },
        "policy": {
            "minimum_recall_delta": 0.0,
            "maximum_category_regression": 0.01,
            "maximum_p95_latency_ms": 500.0,
            "required_categories": ["semantic", "exact"],
            "required_metrics": ["locator_accuracy", "stale_warning_rate"],
        },
    }


def _metrics(*, recall: float, failed: int = 0, unauthorized: int = 0) -> dict[str, object]:
    return {
        "case_count": 10,
        "failed_case_count": failed,
        "recall_at_k": recall,
        "mrr": recall,
        "ndcg_at_k": recall,
        "zero_result_rate": 0.0,
        "unauthorized_result_count": unauthorized,
        "locator_accuracy": 1.0,
        "latest_version_accuracy": 1.0,
        "stale_warning_rate": 1.0,
    }


def _report() -> dict[str, object]:
    baseline = {
        "metrics": _metrics(recall=0.90),
        "latency_ms": {"p50": 20.0, "p95": 40.0, "max": 60.0},
        "categories": {
            "semantic": _metrics(recall=0.80),
            "exact": _metrics(recall=1.0),
        },
    }
    candidate = {
        "metrics": _metrics(recall=0.94),
        "latency_ms": {"p50": 60.0, "p95": 100.0, "max": 140.0},
        "categories": {
            "semantic": _metrics(recall=0.86),
            "exact": _metrics(recall=1.0),
        },
    }
    return {
        "schema_version": "kip.evaluation-report.v1",
        "fingerprints": _manifest()["fingerprints"],
        "variants": {"hybrid": baseline, "reranked": candidate},
    }


def test_experiment_parses_when_revisions_and_fingerprints_are_pinned() -> None:
    # Given a fully pinned experiment manifest
    payload = _manifest()

    # When the boundary parses it
    experiment = QualityExperiment.model_validate(payload)

    # Then the candidate component is immutable and typed
    assert experiment.components[0].revision == "f3f3c8a"
    assert experiment.components[0].kind.value == "reranker"


@pytest.mark.parametrize("revision", ["latest", "main", "HEAD", "unpinned"])
def test_experiment_rejects_mutable_revision_when_candidate_is_registered(
    revision: str,
) -> None:
    # Given a candidate whose revision can move
    payload = _manifest()
    components = payload["components"]
    assert isinstance(components, list)
    component = components[0]
    assert isinstance(component, dict)
    component["revision"] = revision

    # When/Then it crosses the manifest boundary
    with pytest.raises(PydanticValidationError, match="immutable revision"):
        QualityExperiment.model_validate(payload)


def test_experiment_rejects_duplicate_component_kind() -> None:
    # Given two candidate definitions for the same pipeline component
    payload = _manifest()
    components = payload["components"]
    assert isinstance(components, list)
    components.append(deepcopy(components[0]))

    # When/Then the experiment is parsed
    with pytest.raises(PydanticValidationError, match="component kinds must be unique"):
        QualityExperiment.model_validate(payload)


def test_experiment_rejects_missing_fingerprint() -> None:
    # Given an experiment that cannot be reproduced
    payload = _manifest()
    fingerprints = payload["fingerprints"]
    assert isinstance(fingerprints, dict)
    del fingerprints["corpus"]

    # When/Then it crosses the manifest boundary
    with pytest.raises(PydanticValidationError):
        QualityExperiment.model_validate(payload)


def test_repository_example_experiment_is_valid() -> None:
    # Given the operator-facing example
    path = ROOT / "evaluation/experiments/example.yaml"

    # When it is loaded through the production boundary
    experiment = load_experiment(path)

    # Then it is a reproducible experiment contract
    assert experiment.schema_version == "kip.quality-experiment.v1"


def test_recommendation_promotes_when_every_fail_closed_gate_passes() -> None:
    # Given a candidate that improves recall without regressions
    experiment = QualityExperiment.model_validate(_manifest())
    report = QualityReport.model_validate(_report())

    # When the quality control plane evaluates the artifact
    recommendation = recommend(experiment, report)

    # Then it recommends promotion but performs no activation
    assert recommendation.status == "promote"
    assert all(gate.passed for gate in recommendation.gates)


def _candidate_section(report: dict[str, object], section: str) -> dict[str, object]:
    variants = report["variants"]
    assert isinstance(variants, dict)
    candidate = variants["reranked"]
    assert isinstance(candidate, dict)
    selected = candidate[section]
    assert isinstance(selected, dict)
    return selected


def _candidate_category(report: dict[str, object], category: str) -> dict[str, object]:
    categories = _candidate_section(report, "categories")
    selected = categories[category]
    assert isinstance(selected, dict)
    return selected


def _set_failed_case(report: dict[str, object]) -> None:
    _candidate_section(report, "metrics")["failed_case_count"] = 1


def _set_acl_leak(report: dict[str, object]) -> None:
    _candidate_section(report, "metrics")["unauthorized_result_count"] = 1


def _set_slow_latency(report: dict[str, object]) -> None:
    _candidate_section(report, "latency_ms")["p95"] = 900.0


def _set_exact_regression(report: dict[str, object]) -> None:
    _candidate_category(report, "exact")["recall_at_k"] = 0.8


def _remove_locator_metric(report: dict[str, object]) -> None:
    _candidate_section(report, "metrics")["locator_accuracy"] = None


def _change_code_fingerprint(report: dict[str, object]) -> None:
    fingerprints = report["fingerprints"]
    assert isinstance(fingerprints, dict)
    fingerprints["code"] = "sha256:different"


@pytest.mark.parametrize(
    ("mutate", "failed_gate"),
    [
        (_set_failed_case, "failed_cases"),
        (_set_acl_leak, "acl_leaks"),
        (_set_slow_latency, "latency_p95"),
        (_set_exact_regression, "category_regression"),
        (_remove_locator_metric, "required_metrics"),
        (_change_code_fingerprint, "fingerprints"),
    ],
)
def test_recommendation_keeps_candidate_disabled_when_gate_fails(
    mutate: Callable[[dict[str, object]], None],
    failed_gate: str,
) -> None:
    # Given one unsafe or unreproducible candidate dimension
    payload = _report()
    mutate(payload)
    experiment = QualityExperiment.model_validate(_manifest())
    report = QualityReport.model_validate(payload)

    # When the recommendation is calculated
    recommendation = recommend(experiment, report)

    # Then the candidate remains disabled with a machine-readable gate
    assert recommendation.status == "keep_disabled"
    assert failed_gate in {gate.name for gate in recommendation.gates if not gate.passed}


def test_recommendation_rejects_report_without_declared_candidate_variant() -> None:
    # Given a report that did not execute the experiment candidate
    payload = _report()
    variants = payload["variants"]
    assert isinstance(variants, dict)
    del variants["reranked"]
    experiment = QualityExperiment.model_validate(_manifest())
    report = QualityReport.model_validate(payload)

    # When/Then it is evaluated
    with pytest.raises(ValidationError, match="candidate variant"):
        recommend(experiment, report)


def test_repository_evaluation_report_drives_fail_closed_recommendation() -> None:
    # Given the complete report and manifest produced by the real corpus audit
    experiment = load_experiment(
        ROOT / "evaluation/experiments/quality-audit-20260806-reranker.yaml"
    )
    report = load_quality_report(
        ROOT / "evaluation/reports/quality-audit-20260806-all/latest.json"
    )

    # When the quality control plane evaluates the production artifact shape
    result = recommend(experiment, report)

    # Then unmeasured evidence and excessive latency keep the candidate disabled
    assert result.status == "keep_disabled"
    assert {gate.name for gate in result.gates if not gate.passed} >= {
        "latency_p95",
        "required_metrics",
    }
