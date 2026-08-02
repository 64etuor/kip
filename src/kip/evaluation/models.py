from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedEvidence(EvaluationModel):
    type: str = Field(min_length=1)
    data: dict[str, Any] = Field(default_factory=dict)


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
    recall_at: int = Field(default=10, ge=1, le=100)
    notes: str | None = None


class GoldenDataset(EvaluationModel):
    schema_version: Literal["kip.golden-dataset.v1"] = "kip.golden-dataset.v1"
    name: str = Field(min_length=1)
    description: str | None = None
    corpus_fingerprint: str | None = None
    cases: list[GoldenCase] = Field(min_length=1)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> GoldenDataset:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("golden case ids must be unique")
        return self


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
    recall_at_k: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    ndcg_at_k: float = Field(ge=0, le=1)
    zero_result_rate: float = Field(ge=0, le=1)
    zero_result_recovery_rate: float = Field(default=0, ge=0, le=1)
    unauthorized_result_count: int = Field(ge=0)
    locator_accuracy: float = Field(ge=0, le=1)
    latest_version_accuracy: float = Field(ge=0, le=1)
    stale_warning_rate: float = Field(ge=0, le=1)
