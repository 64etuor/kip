from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

EvaluationDimension = Literal["retrieval", "answer", "ontology"]


def _required_retrieval_dimension() -> list[EvaluationDimension]:
    return ["retrieval"]


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedEvidence(EvaluationModel):
    type: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)


class ExpectedAssertion(EvaluationModel):
    subject_id: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object_entity_id: str | None = None
    object_value: Any = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def exactly_one_object(self) -> Self:
        if (self.object_entity_id is None) == (self.object_value is None):
            raise ValueError("expected assertion requires exactly one object")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("expected assertion evidence IDs must be unique")
        return self


class GoldenCase(EvaluationModel):
    id: str = Field(min_length=2, max_length=128)
    question: str = Field(min_length=1)
    category: str = Field(min_length=1)
    principal: str = Field(min_length=1)
    acl_scopes: list[str] = Field(default_factory=list)
    expected_documents: list[str]
    forbidden_documents: list[str] = Field(default_factory=list)
    expected_evidence: list[ExpectedEvidence] = Field(default_factory=list)
    expected_latest: bool | None = None
    expected_stale_warning: bool | None = None
    lifecycle: Literal["draft", "reviewed", "golden", "challenge", "canary"] = "draft"
    split: Literal["train", "validation", "test", "canary"] = "test"
    version: str = Field(default="draft", min_length=1, max_length=128)
    reviewer: str | None = Field(default=None, min_length=1, max_length=256)
    source_revision: str | None = Field(default=None, min_length=1, max_length=256)
    expected_claims: list[str] = Field(default_factory=list)
    expected_evidence_ids: list[str] = Field(default_factory=list)
    expected_entity_ids: list[str] = Field(default_factory=list)
    expected_assertions: list[ExpectedAssertion] = Field(default_factory=list)
    expected_paths: list[list[str]] = Field(default_factory=list)
    expected_contradictions: list[list[str]] = Field(default_factory=list)
    forbidden_entity_ids: list[str] = Field(default_factory=list)
    forbidden_assertions: list[str] = Field(default_factory=list)
    forbidden_evidence_ids: list[str] = Field(default_factory=list)
    expected_refusal: bool | None = None
    recall_at: int = Field(default=10, ge=1, le=100)
    notes: str | None = None

    @model_validator(mode="after")
    def reviewed_cases_are_immutable(self) -> Self:
        unique_lists = (
            self.acl_scopes,
            self.expected_documents,
            self.forbidden_documents,
            self.expected_claims,
            self.expected_evidence_ids,
            self.expected_entity_ids,
            self.forbidden_entity_ids,
            self.forbidden_assertions,
            self.forbidden_evidence_ids,
        )
        if any(len(values) != len(set(values)) for values in unique_lists):
            raise ValueError("golden case ID lists must contain unique values")
        if any(len(path) != len(set(path)) for path in self.expected_paths):
            raise ValueError("expected paths must not repeat assertion IDs")
        if any(len(pair) != 2 or pair[0] == pair[1] for pair in self.expected_contradictions):
            raise ValueError("expected contradictions must contain two distinct assertion IDs")
        if self.lifecycle != "draft" and (
            self.version == "draft" or self.reviewer is None or self.source_revision is None
        ):
            raise ValueError("reviewed cases require version, reviewer, and source_revision")
        return self


class GoldenDataset(EvaluationModel):
    schema_version: Literal["kip.golden-dataset.v1"] = "kip.golden-dataset.v1"
    name: str = Field(min_length=1)
    description: str | None = None
    corpus_fingerprint: str | None = None
    lifecycle: Literal["draft", "reviewed", "golden", "challenge", "canary"] = "draft"
    version: str = Field(default="draft", min_length=1, max_length=128)
    reviewer: str | None = Field(default=None, min_length=1, max_length=256)
    source_revision: str | None = Field(default=None, min_length=1, max_length=256)
    required_dimensions: list[EvaluationDimension] = Field(
        default_factory=_required_retrieval_dimension
    )
    cases: list[GoldenCase] = Field(min_length=1)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> GoldenDataset:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("golden case ids must be unique")
        if len(self.required_dimensions) != len(set(self.required_dimensions)):
            raise ValueError("required evaluation dimensions must be unique")
        if "retrieval" not in self.required_dimensions:
            raise ValueError("retrieval must remain a required evaluation dimension")
        if self.lifecycle != "draft" and (
            self.version == "draft" or self.reviewer is None or self.source_revision is None
        ):
            raise ValueError("reviewed datasets require version, reviewer, and source_revision")
        return self

    @property
    def gate_eligible(self) -> bool:
        return bool(
            self.lifecycle != "draft"
            and self.version != "draft"
            and self.reviewer
            and self.source_revision
            and self.corpus_fingerprint
            and all(
                case.lifecycle != "draft"
                and case.version != "draft"
                and case.reviewer
                and case.source_revision
                for case in self.cases
            )
        )


class CaseMetrics(EvaluationModel):
    case_id: str
    category: str
    expected_documents: list[str]
    ranked_documents: list[str]
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float
    zero_results: bool
    unauthorized_result_count: int
    locator_match: bool | None = None
    latest_version_match: bool | None = None
    stale_warning_match: bool | None = None
    latency_ms: float = Field(ge=0)
    error: str | None = None


class AggregateMetrics(EvaluationModel):
    case_count: int = Field(ge=0)
    failed_case_count: int = Field(default=0, ge=0)
    recall_at_k: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    ndcg_at_k: float = Field(ge=0, le=1)
    zero_result_rate: float = Field(ge=0, le=1)
    zero_result_recovery_rate: float | None = Field(default=None, ge=0, le=1)
    unauthorized_result_count: int = Field(ge=0)
    locator_accuracy: float | None = Field(default=None, ge=0, le=1)
    latest_version_accuracy: float | None = Field(default=None, ge=0, le=1)
    stale_warning_rate: float | None = Field(default=None, ge=0, le=1)
