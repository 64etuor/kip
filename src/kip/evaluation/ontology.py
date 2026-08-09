from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from statistics import fmean
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kip.evaluation.models import ExpectedAssertion


class OntologyEvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OntologyAssertionObservation(OntologyEvaluationModel):
    assertion_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object_entity_id: str | None = None
    object_value: Any = None
    evidence_ids: tuple[str, ...] = ()
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def valid_assertion(self) -> Self:
        if (self.object_entity_id is None) == (self.object_value is None):
            raise ValueError("ontology observation requires exactly one object")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("ontology observation evidence IDs must be unique")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("ontology observation validity interval is invalid")
        return self


class OntologyPathObservation(OntologyEvaluationModel):
    node_ids: tuple[str, ...] = Field(min_length=1)
    assertion_ids: tuple[str, ...] = Field(min_length=1)


class OntologyContradiction(OntologyEvaluationModel):
    assertion_ids: tuple[str, str]

    @field_validator("assertion_ids")
    @classmethod
    def canonical_pair(cls, values: tuple[str, str]) -> tuple[str, str]:
        if values[0] == values[1]:
            raise ValueError("contradiction requires distinct assertions")
        return (min(values), max(values))


class OntologyReview(OntologyEvaluationModel):
    schema_version: str = "kip.ontology-review.v1"
    case_id: str = Field(min_length=1)
    expected_entity_ids: tuple[str, ...] | None = None
    actual_entity_ids: tuple[str, ...] = ()
    expected_assertions: tuple[ExpectedAssertion, ...] | None = None
    actual_assertions: tuple[OntologyAssertionObservation, ...] = ()
    expected_paths: tuple[tuple[str, ...], ...] | None = None
    actual_paths: tuple[OntologyPathObservation, ...] = ()
    expected_contradictions: tuple[OntologyContradiction, ...] | None = None
    detected_contradictions: tuple[OntologyContradiction, ...] = ()
    as_of: datetime | None = None
    forbidden_entity_ids: tuple[str, ...] = ()
    forbidden_assertion_ids: tuple[str, ...] = ()
    forbidden_evidence_ids: tuple[str, ...] = ()


class OntologyMetrics(OntologyEvaluationModel):
    schema_version: str = "kip.ontology-metrics.v1"
    case_id: str
    entity_precision: float | None = Field(default=None, ge=0, le=1)
    entity_recall: float | None = Field(default=None, ge=0, le=1)
    relation_precision: float | None = Field(default=None, ge=0, le=1)
    relation_recall: float | None = Field(default=None, ge=0, le=1)
    evidence_precision: float | None = Field(default=None, ge=0, le=1)
    evidence_recall: float | None = Field(default=None, ge=0, le=1)
    contradiction_precision: float | None = Field(default=None, ge=0, le=1)
    contradiction_recall: float | None = Field(default=None, ge=0, le=1)
    path_relevance: float | None = Field(default=None, ge=0, le=1)
    path_recall: float | None = Field(default=None, ge=0, le=1)
    temporal_accuracy: float | None = Field(default=None, ge=0, le=1)
    duplicate_count: int = Field(ge=0)
    orphan_count: int = Field(ge=0)
    acl_leakage_count: int = Field(ge=0)


class OntologyAggregateMetrics(OntologyEvaluationModel):
    schema_version: str = "kip.ontology-aggregate-metrics.v1"
    case_count: int = Field(ge=0)
    entity_precision: float | None = Field(default=None, ge=0, le=1)
    entity_recall: float | None = Field(default=None, ge=0, le=1)
    relation_precision: float | None = Field(default=None, ge=0, le=1)
    relation_recall: float | None = Field(default=None, ge=0, le=1)
    evidence_precision: float | None = Field(default=None, ge=0, le=1)
    evidence_recall: float | None = Field(default=None, ge=0, le=1)
    contradiction_precision: float | None = Field(default=None, ge=0, le=1)
    contradiction_recall: float | None = Field(default=None, ge=0, le=1)
    path_relevance: float | None = Field(default=None, ge=0, le=1)
    path_recall: float | None = Field(default=None, ge=0, le=1)
    temporal_accuracy: float | None = Field(default=None, ge=0, le=1)
    duplicate_count: int = Field(ge=0)
    orphan_count: int = Field(ge=0)
    acl_leakage_count: int = Field(ge=0)


def evaluate_ontology(review: OntologyReview) -> OntologyMetrics:
    expected_entities = (
        set(review.expected_entity_ids) if review.expected_entity_ids is not None else None
    )
    actual_entities = set(review.actual_entity_ids)
    entity_precision, entity_recall = _precision_recall(
        expected_entities,
        actual_entities,
    )
    expected_relations = (
        {_expected_key(assertion) for assertion in review.expected_assertions}
        if review.expected_assertions is not None
        else None
    )
    actual_relation_keys = [_actual_key(assertion) for assertion in review.actual_assertions]
    actual_relations = set(actual_relation_keys)
    relation_precision, relation_recall = _precision_recall(
        expected_relations,
        actual_relations,
    )
    expected_evidence = (
        {
            evidence_id
            for assertion in review.expected_assertions
            for evidence_id in assertion.evidence_ids
        }
        if review.expected_assertions is not None
        else None
    )
    actual_evidence = {
        evidence_id
        for assertion in review.actual_assertions
        for evidence_id in assertion.evidence_ids
    }
    evidence_precision, evidence_recall = _precision_recall(
        expected_evidence,
        actual_evidence,
    )
    expected_contradictions = (
        {item.assertion_ids for item in review.expected_contradictions}
        if review.expected_contradictions is not None
        else None
    )
    detected_contradictions = {item.assertion_ids for item in review.detected_contradictions}
    contradiction_precision, contradiction_recall = _precision_recall(
        expected_contradictions,
        detected_contradictions,
    )
    expected_paths = set(review.expected_paths) if review.expected_paths is not None else None
    actual_path_keys = [path.assertion_ids for path in review.actual_paths]
    path_relevance, path_recall = _precision_recall(
        expected_paths,
        set(actual_path_keys),
    )
    temporal_accuracy = _temporal_accuracy(review)
    duplicate_count = (
        len(review.actual_entity_ids)
        - len(actual_entities)
        + len(actual_relation_keys)
        - len(actual_relations)
        + len(actual_path_keys)
        - len(set(actual_path_keys))
    )
    orphan_count = sum(
        assertion.subject_id not in actual_entities
        or (
            assertion.object_entity_id is not None
            and assertion.object_entity_id not in actual_entities
        )
        for assertion in _unique_assertions(review.actual_assertions)
    )
    actual_assertion_ids = {assertion.assertion_id for assertion in review.actual_assertions}
    acl_leakage_count = (
        len(actual_entities.intersection(review.forbidden_entity_ids))
        + len(actual_assertion_ids.intersection(review.forbidden_assertion_ids))
        + len(actual_evidence.intersection(review.forbidden_evidence_ids))
    )
    return OntologyMetrics(
        case_id=review.case_id,
        entity_precision=entity_precision,
        entity_recall=entity_recall,
        relation_precision=relation_precision,
        relation_recall=relation_recall,
        evidence_precision=evidence_precision,
        evidence_recall=evidence_recall,
        contradiction_precision=contradiction_precision,
        contradiction_recall=contradiction_recall,
        path_relevance=path_relevance,
        path_recall=path_recall,
        temporal_accuracy=temporal_accuracy,
        duplicate_count=duplicate_count,
        orphan_count=orphan_count,
        acl_leakage_count=acl_leakage_count,
    )


def aggregate_ontology_metrics(
    metrics: Sequence[OntologyMetrics],
) -> OntologyAggregateMetrics:
    return OntologyAggregateMetrics(
        case_count=len(metrics),
        entity_precision=_average(metrics, "entity_precision"),
        entity_recall=_average(metrics, "entity_recall"),
        relation_precision=_average(metrics, "relation_precision"),
        relation_recall=_average(metrics, "relation_recall"),
        evidence_precision=_average(metrics, "evidence_precision"),
        evidence_recall=_average(metrics, "evidence_recall"),
        contradiction_precision=_average(metrics, "contradiction_precision"),
        contradiction_recall=_average(metrics, "contradiction_recall"),
        path_relevance=_average(metrics, "path_relevance"),
        path_recall=_average(metrics, "path_recall"),
        temporal_accuracy=_average(metrics, "temporal_accuracy"),
        duplicate_count=sum(item.duplicate_count for item in metrics),
        orphan_count=sum(item.orphan_count for item in metrics),
        acl_leakage_count=sum(item.acl_leakage_count for item in metrics),
    )


def _precision_recall(
    expected: set[Any] | None,
    actual: set[Any],
) -> tuple[float | None, float | None]:
    if expected is None:
        return None, None
    matched = len(expected.intersection(actual))
    precision = matched / len(actual) if actual else float(not expected)
    recall = matched / len(expected) if expected else float(not actual)
    return precision, recall


def _expected_key(assertion: ExpectedAssertion) -> tuple[str, str, str]:
    return (
        assertion.subject_id,
        assertion.predicate,
        assertion.object_entity_id
        or json.dumps(assertion.object_value, ensure_ascii=False, sort_keys=True),
    )


def _actual_key(assertion: OntologyAssertionObservation) -> tuple[str, str, str]:
    return (
        assertion.subject_id,
        assertion.predicate,
        assertion.object_entity_id
        or json.dumps(assertion.object_value, ensure_ascii=False, sort_keys=True),
    )


def _unique_assertions(
    assertions: tuple[OntologyAssertionObservation, ...],
) -> list[OntologyAssertionObservation]:
    return list({_actual_key(assertion): assertion for assertion in assertions}.values())


def _temporal_accuracy(review: OntologyReview) -> float | None:
    if review.as_of is None:
        return None
    assertions = _unique_assertions(review.actual_assertions)
    if not assertions:
        return 0.0 if review.expected_assertions else 1.0
    return sum(
        (assertion.valid_from is None or assertion.valid_from <= review.as_of)
        and (assertion.valid_to is None or assertion.valid_to > review.as_of)
        for assertion in assertions
    ) / len(assertions)


def _average(metrics: Sequence[OntologyMetrics], field: str) -> float | None:
    values = [getattr(item, field) for item in metrics if getattr(item, field) is not None]
    return fmean(values) if values else None
