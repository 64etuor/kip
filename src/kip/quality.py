from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)
from pydantic import (
    ValidationError as PydanticValidationError,
)

from kip.errors import ValidationError


class QualityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ComponentKind(StrEnum):
    PARSER = "parser"
    EMBEDDING = "embedding"
    RERANKER = "reranker"
    RETRIEVAL = "retrieval"


class QualityComponent(QualityModel):
    kind: ComponentKind
    adapter: str = Field(min_length=1)
    identifier: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("revision")
    @classmethod
    def revision_is_immutable(cls, value: str) -> str:
        if value.casefold() in {"latest", "main", "head", "unpinned"}:
            raise ValueError("component requires an immutable revision")
        return value


class QualityFingerprints(QualityModel):
    corpus: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    configuration: str = Field(min_length=1)
    code: str = Field(min_length=1)


MetricName = Literal[
    "locator_accuracy",
    "latest_version_accuracy",
    "stale_warning_rate",
]


class PromotionPolicy(QualityModel):
    minimum_recall_delta: float = Field(default=0.0, ge=-1.0, le=1.0)
    maximum_category_regression: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum_p95_latency_ms: float = Field(gt=0.0)
    minimum_required_metric: float = Field(default=1.0, ge=0.0, le=1.0)
    required_categories: tuple[str, ...] = ()
    required_metrics: tuple[MetricName, ...] = ()


class QualityExperiment(QualityModel):
    schema_version: Literal["kip.quality-experiment.v1"]
    id: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    baseline_variant: str = Field(min_length=1)
    candidate_variant: str = Field(min_length=1)
    components: tuple[QualityComponent, ...] = Field(min_length=1)
    fingerprints: QualityFingerprints
    policy: PromotionPolicy

    @model_validator(mode="after")
    def component_kinds_are_unique(self) -> Self:
        kinds = [component.kind for component in self.components]
        if len(kinds) != len(set(kinds)):
            raise ValueError("component kinds must be unique")
        if self.baseline_variant == self.candidate_variant:
            raise ValueError("baseline and candidate variants must differ")
        return self


class QualityMetrics(QualityModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    case_count: int = Field(ge=0)
    failed_case_count: int = Field(default=0, ge=0)
    recall_at_k: float = Field(ge=0.0, le=1.0)
    mrr: float = Field(ge=0.0, le=1.0)
    ndcg_at_k: float = Field(ge=0.0, le=1.0)
    zero_result_rate: float = Field(ge=0.0, le=1.0)
    unauthorized_result_count: int = Field(ge=0)
    locator_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    latest_version_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    stale_warning_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class QualityLatency(QualityModel):
    p50: float = Field(ge=0.0)
    p95: float = Field(ge=0.0)
    max: float = Field(ge=0.0)


class QualityVariant(QualityModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    metrics: QualityMetrics
    latency_ms: QualityLatency
    categories: dict[str, QualityMetrics]


class QualityReport(QualityModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: Literal["kip.evaluation-report.v1"]
    fingerprints: QualityFingerprints
    variants: dict[str, QualityVariant] = Field(min_length=1)


GateValue = bool | int | float | str | None


class QualityGate(QualityModel):
    name: str
    passed: bool
    value: GateValue
    threshold: GateValue


class QualityRecommendation(QualityModel):
    schema_version: Literal["kip.quality-recommendation.v1"] = (
        "kip.quality-recommendation.v1"
    )
    experiment_id: str
    status: Literal["promote", "keep_disabled"]
    gates: tuple[QualityGate, ...]
    reasons: tuple[str, ...]


def load_experiment(path: Path) -> QualityExperiment:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValidationError(f"cannot load quality experiment: {error}") from error
    try:
        return QualityExperiment.model_validate(payload)
    except PydanticValidationError as error:
        raise ValidationError(f"invalid quality experiment: {error}") from error


def load_quality_report(path: Path) -> QualityReport:
    try:
        payload = QualityReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, PydanticValidationError) as error:
        raise ValidationError(f"invalid quality report: {error}") from error
    return payload


def _gate(name: str, value: GateValue, threshold: GateValue, passed: bool) -> QualityGate:
    return QualityGate(name=name, value=value, threshold=threshold, passed=passed)


def recommend(
    experiment: QualityExperiment,
    report: QualityReport,
) -> QualityRecommendation:
    baseline = report.variants.get(experiment.baseline_variant)
    candidate = report.variants.get(experiment.candidate_variant)
    if baseline is None:
        raise ValidationError("evaluation report is missing the baseline variant")
    if candidate is None:
        raise ValidationError("evaluation report is missing the candidate variant")

    policy = experiment.policy
    recall_delta = candidate.metrics.recall_at_k - baseline.metrics.recall_at_k
    category_deltas = [
        candidate.categories[name].recall_at_k - baseline.categories[name].recall_at_k
        for name in policy.required_categories
        if name in baseline.categories and name in candidate.categories
    ]
    category_coverage = len(category_deltas) == len(policy.required_categories)
    worst_category_delta = min(category_deltas, default=0.0)
    metric_values = [getattr(candidate.metrics, name) for name in policy.required_metrics]
    metrics_measured = all(value is not None for value in metric_values)
    metrics_pass = metrics_measured and all(
        value is not None and value >= policy.minimum_required_metric
        for value in metric_values
    )

    gates = (
        _gate(
            "fingerprints",
            report.fingerprints == experiment.fingerprints,
            True,
            report.fingerprints == experiment.fingerprints,
        ),
        _gate(
            "overall_recall",
            recall_delta,
            policy.minimum_recall_delta,
            recall_delta >= policy.minimum_recall_delta,
        ),
        _gate(
            "category_coverage",
            len(category_deltas),
            len(policy.required_categories),
            category_coverage,
        ),
        _gate(
            "category_regression",
            worst_category_delta,
            -policy.maximum_category_regression,
            category_coverage
            and worst_category_delta >= -policy.maximum_category_regression,
        ),
        _gate(
            "failed_cases",
            candidate.metrics.failed_case_count,
            0,
            candidate.metrics.failed_case_count == 0,
        ),
        _gate(
            "acl_leaks",
            candidate.metrics.unauthorized_result_count,
            0,
            candidate.metrics.unauthorized_result_count == 0,
        ),
        _gate(
            "latency_p95",
            candidate.latency_ms.p95,
            policy.maximum_p95_latency_ms,
            candidate.latency_ms.p95 <= policy.maximum_p95_latency_ms,
        ),
        _gate(
            "required_metrics",
            min((value for value in metric_values if value is not None), default=None),
            policy.minimum_required_metric,
            metrics_pass,
        ),
    )
    failed = tuple(gate.name for gate in gates if not gate.passed)
    return QualityRecommendation(
        experiment_id=experiment.id,
        status="keep_disabled" if failed else "promote",
        gates=gates,
        reasons=failed,
    )
