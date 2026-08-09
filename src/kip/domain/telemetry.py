from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kip.ids import new_id

TraceRoute = Literal["search", "context", "answer", "ontology_mining"]
TraceOutcome = Literal["succeeded", "refused", "degraded", "failed"]
TraceStage = Literal[
    "acl_prefilter",
    "lexical",
    "vector",
    "fusion",
    "rerank",
    "exact_evidence_read",
    "ontology_context",
    "model_egress_policy",
    "structured_generation",
    "citation_validation",
    "candidate_persistence",
]
TraceCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]

_SAFE_REQUEST_ID = re.compile(r"^req_[0-9a-f]{32}$")


class TelemetryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QueryFilterSummary(TelemetryModel):
    source_kind_count: int = Field(default=0, ge=0, le=100)
    document_type_count: int = Field(default=0, ge=0, le=100)
    project_id_count: int = Field(default=0, ge=0, le=100)
    includes_candidate_assertions: bool = False
    limit: int = Field(default=10, ge=1, le=1000)


class QueryTraceCandidate(TelemetryModel):
    unit_id: str = Field(min_length=1, max_length=128)
    rank: int = Field(ge=1, le=1000)
    score: float
    channels: tuple[Literal["lexical", "vector"], ...] = ()


class QueryTraceModelRevision(TelemetryModel):
    role: Literal["embedding", "reranker", "generation", "relation_miner"]
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    revision: str = Field(min_length=1, max_length=256)


class QueryTraceUsage(TelemetryModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class QueryTrace(TelemetryModel):
    schema_version: Literal["kip.query-trace.v1"] = "kip.query-trace.v1"
    id: str = Field(default_factory=lambda: new_id("qtrace"), min_length=1, max_length=128)
    request_id: str | None = None
    route: TraceRoute
    outcome: TraceOutcome
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float = Field(ge=0)
    filters: QueryFilterSummary = Field(default_factory=QueryFilterSummary)
    stages: list[TraceStage] = Field(default_factory=list, max_length=32)
    candidates: list[QueryTraceCandidate] = Field(default_factory=list, max_length=100)
    selected_evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    ontology_assertion_ids: list[str] = Field(default_factory=list, max_length=500)
    acl_policy_version: str | None = Field(default=None, max_length=256)
    models: list[QueryTraceModelRevision] = Field(default_factory=list, max_length=8)
    warnings: list[TraceCode] = Field(default_factory=list, max_length=32)
    usage: QueryTraceUsage | None = None
    refusal_reason: TraceCode | None = None

    @field_validator(
        "stages",
        "selected_evidence_ids",
        "ontology_assertion_ids",
        "warnings",
    )
    @classmethod
    def unique_lists(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("query trace lists must contain unique values")
        return values


def safe_request_id(value: str | None) -> str | None:
    if value is None or _SAFE_REQUEST_ID.fullmatch(value) is None:
        return None
    return value
