from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _schema(name: str) -> dict:
    path = ROOT / "evaluation" / "schemas" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_golden_case_schema_accepts_minimal_case() -> None:
    case = {
        "id": "GQ-001",
        "question": "참여율 변경 승인 근거는?",
        "category": "semantic_paraphrase",
        "principal": "principal_public",
        "acl_scopes": ["workspace:default", "public"],
        "expected_documents": ["ldoc_example"],
        "forbidden_documents": [],
        "recall_at": 10,
    }

    jsonschema.validate(case, _schema("golden-case.schema.json"))


def test_golden_case_schema_requires_expected_documents() -> None:
    case = {
        "id": "GQ-002",
        "question": "문서 번호는?",
        "category": "exact_identifier",
        "principal": "principal_public",
        "acl_scopes": ["public"],
        "forbidden_documents": [],
        "recall_at": 10,
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(case, _schema("golden-case.schema.json"))


def test_evaluation_report_schema_accepts_minimal_report() -> None:
    report = {
        "schema_version": "kip.evaluation-report.v1",
        "run": {
            "id": "eval_20260730T000000Z",
            "started_at": "2026-07-30T00:00:00Z",
            "completed_at": "2026-07-30T00:00:01Z",
        },
        "fingerprints": {
            "corpus": "sha256:corpus",
            "dataset": "sha256:dataset",
            "configuration": "sha256:config",
            "code": "sha256:code",
        },
        "variants": {
            "lexical": {
                "metrics": {
                    "case_count": 1,
                    "recall_at_k": 1.0,
                    "mrr": 1.0,
                    "ndcg_at_k": 1.0,
                    "zero_result_rate": 0.0,
                    "unauthorized_result_count": 0,
                    "locator_accuracy": 1.0,
                    "latest_version_accuracy": 1.0,
                    "stale_warning_rate": 1.0,
                },
                "latency_ms": {"p50": 1.0, "p95": 1.0, "max": 1.0},
                "categories": {},
                "cases": [],
            }
        },
        "gates": {},
        "decision": {"status": "baseline", "reasons": []},
    }

    jsonschema.validate(report, _schema("evaluation-report.schema.json"))
