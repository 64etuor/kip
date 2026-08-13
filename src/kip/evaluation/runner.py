from __future__ import annotations

import hashlib
import json
import platform
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

import yaml

from kip.domain.models import SearchHit
from kip.errors import ValidationError
from kip.evaluation.answers import AnswerReview, aggregate_answer_metrics, evaluate_answer
from kip.evaluation.metrics import (
    deduplicate_ranked_documents,
    forbidden_document_count,
    locator_matches,
    ndcg_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank,
)
from kip.evaluation.models import AggregateMetrics, CaseMetrics, GoldenCase, GoldenDataset
from kip.evaluation.ontology import (
    OntologyContradiction,
    OntologyReview,
    aggregate_ontology_metrics,
    evaluate_ontology,
)
from kip.evaluation.reviews import EvaluationReviewBundle

SearchExecutor = Callable[[GoldenCase, str], list[SearchHit]]
HitEnricher = Callable[[GoldenCase, list[SearchHit]], list[SearchHit]]

ALLOWED_VARIANTS = frozenset({"lexical", "vector", "hybrid", "reranked"})


def requires_stale_enrichment(dataset: GoldenDataset) -> bool:
    return any(case.expected_stale_warning is not None for case in dataset.cases)


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def load_dataset(path: Path) -> GoldenDataset:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return GoldenDataset.model_validate(raw)


def _code_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    paths: list[Path] = []
    for relative in ("src/kip", "migrations", "pyproject.toml", "uv.lock"):
        candidate = root / relative
        if candidate.is_file():
            paths.append(candidate)
        elif candidate.is_dir():
            paths.extend(path for path in candidate.rglob("*") if path.is_file())
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _configuration_fingerprint(configuration: dict[str, Any]) -> str:
    payload = json.dumps(configuration, ensure_ascii=False, sort_keys=True, default=str).encode()
    return _sha256(payload)


def code_fingerprint(root: Path) -> str:
    return _code_fingerprint(root)


def configuration_fingerprint(configuration: dict[str, Any]) -> str:
    return _configuration_fingerprint(configuration)


def _case_result(case: GoldenCase, hits: list[SearchHit], latency_ms: float) -> CaseMetrics:
    ranked_documents = deduplicate_ranked_documents(hit.document_id for hit in hits)
    expected = set(case.expected_documents)
    if expected:
        reciprocal = reciprocal_rank(ranked_documents, expected)
        ndcg = ndcg_at_k(ranked_documents, expected, case.recall_at)
    else:
        reciprocal = 1.0 if not ranked_documents else 0.0
        ndcg = reciprocal

    relevant_hits = [hit for hit in hits if hit.document_id in expected]
    locator_match: bool | None = None
    if case.expected_evidence:
        expected_locators = [item.model_dump(mode="json") for item in case.expected_evidence]
        locator_match = any(
            locator_matches(hit.locator.model_dump(mode="json"), expected_locators)
            for hit in relevant_hits
        )

    latest_match: bool | None = None
    if case.expected_latest is not None:
        latest_match = any(
            hit.metadata.get("is_latest", True) is case.expected_latest for hit in relevant_hits
        )

    stale_match: bool | None = None
    if case.expected_stale_warning is not None:
        stale_match = any(
            hit.metadata.get("source_changed_since_index") is case.expected_stale_warning
            for hit in relevant_hits
        )

    return CaseMetrics(
        case_id=case.id,
        category=case.category,
        expected_documents=case.expected_documents,
        ranked_documents=ranked_documents,
        recall_at_k=recall_at_k(ranked_documents, expected, case.recall_at),
        reciprocal_rank=reciprocal,
        ndcg_at_k=ndcg,
        zero_results=not hits,
        unauthorized_result_count=forbidden_document_count(
            ranked_documents, set(case.forbidden_documents)
        ),
        locator_match=locator_match,
        latest_version_match=latest_match,
        stale_warning_match=stale_match,
        latency_ms=latency_ms,
    )


def _failed_case(case: GoldenCase, error: Exception, latency_ms: float) -> CaseMetrics:
    return CaseMetrics(
        case_id=case.id,
        category=case.category,
        expected_documents=case.expected_documents,
        ranked_documents=[],
        recall_at_k=0.0,
        reciprocal_rank=0.0,
        ndcg_at_k=0.0,
        zero_results=True,
        unauthorized_result_count=0,
        latency_ms=latency_ms,
        error=f"{type(error).__name__}: {error}",
    )


def _average_optional(results: Sequence[CaseMetrics], field: str) -> float | None:
    values = [getattr(result, field) for result in results if getattr(result, field) is not None]
    return fmean(values) if values else None


def _aggregate(results: Sequence[CaseMetrics]) -> AggregateMetrics:
    count = len(results)
    if not count:
        return AggregateMetrics(
            case_count=0,
            failed_case_count=0,
            recall_at_k=0,
            mrr=0,
            ndcg_at_k=0,
            zero_result_rate=0,
            unauthorized_result_count=0,
        )
    return AggregateMetrics(
        case_count=count,
        failed_case_count=sum(result.error is not None for result in results),
        recall_at_k=fmean(result.recall_at_k for result in results),
        mrr=fmean(result.reciprocal_rank for result in results),
        ndcg_at_k=fmean(result.ndcg_at_k for result in results),
        zero_result_rate=fmean(result.zero_results for result in results),
        unauthorized_result_count=sum(result.unauthorized_result_count for result in results),
        locator_accuracy=_average_optional(results, "locator_match"),
        latest_version_accuracy=_average_optional(results, "latest_version_match"),
        stale_warning_rate=_average_optional(results, "stale_warning_match"),
    )


def _evaluate_variant(
    dataset: GoldenDataset,
    variant: str,
    search: SearchExecutor,
    enrich: HitEnricher | None = None,
) -> dict[str, Any]:
    results: list[CaseMetrics] = []
    for case in dataset.cases:
        started = time.perf_counter_ns()
        try:
            hits = search(case, variant)
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            # Enrichment (for example reopening evidence to measure stale
            # warnings) runs outside the latency window on purpose.
            if enrich is not None:
                hits = enrich(case, hits)
            results.append(_case_result(case, hits, elapsed))
        except Exception as error:
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            results.append(_failed_case(case, error, elapsed))

    categories: dict[str, list[CaseMetrics]] = defaultdict(list)
    for result in results:
        categories[result.category].append(result)
    latencies = [result.latency_ms for result in results]
    payload: dict[str, Any] = {
        "metrics": _aggregate(results).model_dump(mode="json"),
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies, default=0.0),
        },
        "categories": {
            name: _aggregate(category_results).model_dump(mode="json")
            for name, category_results in sorted(categories.items())
        },
        "cases": [result.model_dump(mode="json") for result in results],
    }
    errors = [result.error for result in results if result.error]
    if errors:
        payload["error"] = f"{len(errors)} case(s) failed; first: {errors[0]}"
    return payload


def _warm_variant(
    dataset: GoldenDataset,
    variant: str,
    search: SearchExecutor,
    passes: int,
) -> None:
    for _ in range(passes):
        for case in dataset.cases:
            try:
                search(case, variant)
            except Exception:
                continue


def _validate_answer_review(case: GoldenCase, review: AnswerReview) -> None:
    if review.expected_claims != tuple(case.expected_claims):
        raise ValueError(f"answer review expectations do not match case {case.id}")
    if review.expected_evidence_ids != tuple(case.expected_evidence_ids):
        raise ValueError(f"answer review evidence does not match case {case.id}")
    if review.expected_refusal is not case.expected_refusal:
        raise ValueError(f"answer review refusal does not match case {case.id}")


def _validate_ontology_review(case: GoldenCase, review: OntologyReview) -> None:
    expected_contradictions = tuple(
        OntologyContradiction(assertion_ids=(pair[0], pair[1]))
        for pair in case.expected_contradictions
    )
    expected = (
        review.expected_entity_ids == tuple(case.expected_entity_ids),
        review.expected_assertions == tuple(case.expected_assertions),
        review.expected_paths == tuple(tuple(path) for path in case.expected_paths),
        review.expected_contradictions == expected_contradictions,
        review.forbidden_entity_ids == tuple(case.forbidden_entity_ids),
        review.forbidden_assertion_ids == tuple(case.forbidden_assertions),
        review.forbidden_evidence_ids == tuple(case.forbidden_evidence_ids),
    )
    if not all(expected):
        raise ValueError(f"ontology review expectations do not match case {case.id}")


def _attach_reviewed_quality(
    dataset: GoldenDataset,
    variant: str,
    result: dict[str, Any],
    review_bundle: EvaluationReviewBundle | None,
) -> None:
    if review_bundle is None or variant not in review_bundle.variants:
        return
    cases = {case.id: case for case in dataset.cases}
    reviews = review_bundle.variants[variant]
    answer_metrics = []
    for answer_review in reviews.answer:
        case = cases.get(answer_review.case_id)
        if case is None:
            raise ValueError(
                f"answer review references unknown case {answer_review.case_id}"
            )
        _validate_answer_review(case, answer_review)
        answer_metrics.append(evaluate_answer(answer_review))
    if answer_metrics:
        result["answer_quality"] = {
            "metrics": aggregate_answer_metrics(answer_metrics).model_dump(mode="json"),
            "cases": [metric.model_dump(mode="json") for metric in answer_metrics],
        }
    ontology_metrics = []
    for ontology_review in reviews.ontology:
        case = cases.get(ontology_review.case_id)
        if case is None:
            raise ValueError(
                f"ontology review references unknown case {ontology_review.case_id}"
            )
        _validate_ontology_review(case, ontology_review)
        ontology_metrics.append(evaluate_ontology(ontology_review))
    if ontology_metrics:
        result["ontology_quality"] = {
            "metrics": aggregate_ontology_metrics(ontology_metrics).model_dump(mode="json"),
            "cases": [metric.model_dump(mode="json") for metric in ontology_metrics],
        }


def run_evaluation(
    dataset: GoldenDataset,
    *,
    variants: Sequence[str],
    search: SearchExecutor,
    workspace: str,
    dataset_bytes: bytes,
    configuration: dict[str, Any],
    code_root: Path,
    review_bundle: EvaluationReviewBundle | None = None,
    warmup_passes: int = 0,
    now: Callable[[], datetime] | None = None,
    enrich: HitEnricher | None = None,
) -> dict[str, Any]:
    selected = list(dict.fromkeys(variants))
    unknown = [variant for variant in selected if variant not in ALLOWED_VARIANTS]
    if not selected or unknown:
        raise ValueError(
            "variants must contain one or more of: " + ", ".join(sorted(ALLOWED_VARIANTS))
        )
    if warmup_passes < 0:
        raise ValueError("warmup_passes must be non-negative")
    if review_bundle is not None and (
        review_bundle.dataset_name != dataset.name
        or review_bundle.dataset_version != dataset.version
        or review_bundle.dataset_source_revision != dataset.source_revision
    ):
        raise ValueError("review bundle does not match the evaluated dataset version")
    clock = now or (lambda: datetime.now(UTC))
    started = clock()
    run_id = "eval_" + started.strftime("%Y%m%dT%H%M%S%fZ")
    evaluated: dict[str, dict[str, Any]] = {}
    for variant in selected:
        _warm_variant(dataset, variant, search, warmup_passes)
        evaluated[variant] = _evaluate_variant(dataset, variant, search, enrich)
        _attach_reviewed_quality(
            dataset,
            variant,
            evaluated[variant],
            review_bundle,
        )
    completed = clock()
    has_errors = any("error" in result for result in evaluated.values())
    return {
        "schema_version": "kip.evaluation-report.v1",
        "run": {
            "id": run_id,
            "started_at": started.isoformat().replace("+00:00", "Z"),
            "completed_at": completed.isoformat().replace("+00:00", "Z"),
            "workspace": workspace,
            "dataset": dataset.name,
            "dataset_version": dataset.version,
            "dataset_lifecycle": dataset.lifecycle,
            "dataset_source_revision": dataset.source_revision,
            "dataset_gate_eligible": dataset.gate_eligible,
            "required_dimensions": dataset.required_dimensions,
            "warmup_passes": warmup_passes,
            "hardware": {
                "platform": platform.system(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
        },
        "fingerprints": {
            "corpus": dataset.corpus_fingerprint or "sha256:unspecified",
            "dataset": _sha256(dataset_bytes),
            "configuration": _configuration_fingerprint(configuration),
            "code": _code_fingerprint(code_root),
        },
        "variants": evaluated,
        "gates": {},
        "decision": {
            "status": "incomplete" if has_errors else "baseline",
            "reasons": ["one or more evaluation cases failed"] if has_errors else [],
        },
    }


def compare_variants(
    report: dict[str, Any],
    baseline: str,
    candidate: str,
) -> dict[str, Any]:
    variants = report.get("variants", {})
    if baseline not in variants or candidate not in variants:
        raise ValueError("baseline and candidate must both exist in the report")
    baseline_result = variants[baseline]
    candidate_result = variants[candidate]
    baseline_metrics = baseline_result["metrics"]
    candidate_metrics = candidate_result["metrics"]
    overall_delta = candidate_metrics["recall_at_k"] - baseline_metrics["recall_at_k"]

    category_names = set(baseline_result.get("categories", {})).intersection(
        candidate_result.get("categories", {})
    )
    semantic_deltas = [
        candidate_result["categories"][name]["recall_at_k"]
        - baseline_result["categories"][name]["recall_at_k"]
        for name in category_names
        if "semantic" in name or "paraphrase" in name
    ]
    semantic_delta = fmean(semantic_deltas) if semantic_deltas else 0.0
    worst_semantic_delta = min(semantic_deltas, default=0.0)
    exact_deltas = [
        candidate_result["categories"][name]["recall_at_k"]
        - baseline_result["categories"][name]["recall_at_k"]
        for name in category_names
        if "exact" in name or "identifier" in name
    ]
    exact_delta = min(exact_deltas, default=0.0)

    run = report.get("run", {})
    required_dimensions = set(run.get("required_dimensions", ["retrieval"]))
    gates = {
        "reviewed_dataset": {
            "value": run.get("dataset_gate_eligible"),
            "required": True,
            "passed": run.get("dataset_gate_eligible") is True,
        },
        "overall_recall_improvement": {
            "value": overall_delta,
            "threshold": 0.03,
            "passed": overall_delta >= 0.03,
        },
        "semantic_recall_improvement": {
            "value": semantic_delta,
            "threshold": 0.10,
            "passed": semantic_delta >= 0.10,
        },
        "semantic_regression": {
            "value": worst_semantic_delta,
            "minimum": -0.01,
            "passed": worst_semantic_delta >= -0.01,
        },
        "exact_regression": {
            "value": exact_delta,
            "minimum": -0.01,
            "passed": exact_delta >= -0.01,
        },
        "unauthorized_results": {
            "value": candidate_metrics["unauthorized_result_count"],
            "maximum": 0,
            "passed": candidate_metrics["unauthorized_result_count"] == 0,
        },
        "latency_p95_ms": {
            "value": candidate_result["latency_ms"]["p95"],
            "maximum": 2000.0,
            "passed": candidate_result["latency_ms"]["p95"] <= 2000.0,
        },
        "stale_warning_rate": {
            "value": candidate_metrics.get("stale_warning_rate"),
            "minimum": 1.0,
            "passed": candidate_metrics.get("stale_warning_rate") == 1.0,
        },
        "failed_cases": {
            "value": candidate_metrics.get("failed_case_count", 0),
            "maximum": 0,
            "passed": candidate_metrics.get("failed_case_count", 0) == 0,
        },
    }
    required_quality_gates: list[str] = []
    if "answer" in required_dimensions:
        answer_quality = candidate_result.get("answer_quality", {})
        answer_metrics = answer_quality.get("metrics", {})
        answer_cases = answer_quality.get("cases", [])
        answer_coverage = len(answer_cases)
        expected_coverage = candidate_metrics.get("case_count")
        gates["answer_review_coverage"] = {
            "value": answer_coverage,
            "minimum": expected_coverage,
            "passed": answer_coverage == expected_coverage,
        }
        required_quality_gates.append("answer_review_coverage")
        for metric in (
            "claim_precision",
            "claim_recall",
            "citation_precision",
            "citation_recall",
            "refusal_appropriateness",
        ):
            name = f"answer_{metric}"
            value = answer_metrics.get(metric)
            gates[name] = {
                "value": value,
                "minimum": 1.0,
                "passed": value == 1.0
                and answer_coverage == expected_coverage
                and all(case.get(metric) == 1.0 for case in answer_cases),
            }
            required_quality_gates.append(name)
        unsupported = answer_metrics.get("unsupported_claim_count")
        gates["answer_unsupported_claims"] = {
            "value": unsupported,
            "maximum": 0,
            "passed": unsupported == 0,
        }
        required_quality_gates.append("answer_unsupported_claims")
    if "ontology" in required_dimensions:
        ontology_quality = candidate_result.get("ontology_quality", {})
        ontology_metrics = ontology_quality.get("metrics", {})
        ontology_cases = ontology_quality.get("cases", [])
        ontology_coverage = len(ontology_cases)
        expected_coverage = candidate_metrics.get("case_count")
        gates["ontology_review_coverage"] = {
            "value": ontology_coverage,
            "minimum": expected_coverage,
            "passed": ontology_coverage == expected_coverage,
        }
        required_quality_gates.append("ontology_review_coverage")
        for metric in (
            "entity_precision",
            "entity_recall",
            "relation_precision",
            "relation_recall",
            "evidence_precision",
            "evidence_recall",
            "contradiction_precision",
            "contradiction_recall",
            "path_relevance",
            "path_recall",
            "temporal_accuracy",
        ):
            name = f"ontology_{metric}"
            value = ontology_metrics.get(metric)
            gates[name] = {
                "value": value,
                "minimum": 1.0,
                "passed": value == 1.0
                and ontology_coverage == expected_coverage
                and all(case.get(metric) == 1.0 for case in ontology_cases),
            }
            required_quality_gates.append(name)
        for metric in ("duplicate_count", "orphan_count", "acl_leakage_count"):
            name = f"ontology_{metric}"
            value = ontology_metrics.get(metric)
            gates[name] = {
                "value": value,
                "maximum": 0,
                "passed": value == 0,
            }
            required_quality_gates.append(name)
    quality_improved = (
        gates["overall_recall_improvement"]["passed"]
        or gates["semantic_recall_improvement"]["passed"]
    )
    mandatory = all(
        gates[name]["passed"]
        for name in (
            "exact_regression",
            "semantic_regression",
            "unauthorized_results",
            "latency_p95_ms",
            "stale_warning_rate",
            "failed_cases",
            "reviewed_dataset",
            *required_quality_gates,
        )
    )
    promoted = quality_improved and mandatory
    reasons = []
    if promoted:
        reasons.append("candidate passed quality, security, and latency activation gates")
    else:
        reasons.extend(name for name, gate in gates.items() if not gate["passed"])
    return {
        "baseline": baseline,
        "candidate": candidate,
        "deltas": {
            "overall_recall_at_k": overall_delta,
            "semantic_recall_at_k": semantic_delta,
            "exact_recall_at_k": exact_delta,
        },
        "gates": gates,
        "decision": {
            "status": "promote" if promoted else "keep_disabled",
            "reasons": reasons,
        },
    }


def validate_activation_report(
    report: dict[str, Any],
    *,
    candidate: str,
    configuration: dict[str, Any],
    code_root: Path,
) -> dict[str, Any]:
    if report.get("schema_version") != "kip.evaluation-report.v1":
        raise ValidationError("activation requires a kip.evaluation-report.v1 report")
    if candidate not in {"vector", "hybrid", "reranked"}:
        raise ValidationError("activation candidate must be vector, hybrid, or reranked")
    fingerprints = report.get("fingerprints", {})
    if fingerprints.get("configuration") != configuration_fingerprint(configuration):
        raise ValidationError(
            "evaluation configuration fingerprint does not match current settings"
        )
    if fingerprints.get("code") != code_fingerprint(code_root):
        raise ValidationError("evaluation code fingerprint does not match current code")
    decision = compare_variants(report, "lexical", candidate)
    if decision["decision"]["status"] != "promote":
        raise ValidationError(f"evaluation report does not promote {candidate}")
    return decision
