from __future__ import annotations

import json
from pathlib import Path

from kip.evaluation.reporting import append_evolution_record, write_report


def _report() -> dict:
    return {
        "schema_version": "kip.evaluation-report.v1",
        "run": {
            "id": "eval_fixture",
            "started_at": "2026-07-30T00:00:00Z",
            "completed_at": "2026-07-30T00:00:01Z",
            "workspace": "default",
            "dataset": "fixture.yaml",
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
                    "case_count": 2,
                    "failed_case_count": 0,
                    "recall_at_k": 0.5,
                    "mrr": 0.5,
                    "ndcg_at_k": 0.5,
                    "zero_result_rate": 0.5,
                    "zero_result_recovery_rate": 0.0,
                    "unauthorized_result_count": 0,
                    "locator_accuracy": 1.0,
                    "latest_version_accuracy": 1.0,
                    "stale_warning_rate": 1.0,
                },
                "latency_ms": {"p50": 5.0, "p95": 9.0, "max": 10.0},
                "categories": {},
                "cases": [],
            }
        },
        "gates": {},
        "decision": {"status": "baseline", "reasons": []},
    }


def test_write_report_creates_versioned_and_latest_files(tmp_path: Path) -> None:
    paths = write_report(_report(), tmp_path)

    assert json.loads(paths.json_path.read_text(encoding="utf-8"))["run"]["id"] == "eval_fixture"
    assert (tmp_path / "latest.json").read_bytes() == paths.json_path.read_bytes()
    assert "# KIP Retrieval Evaluation" in paths.markdown_path.read_text(encoding="utf-8")
    assert (tmp_path / "latest.md").read_bytes() == paths.markdown_path.read_bytes()


def test_append_evolution_record_is_compact_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "evolution.jsonl"
    append_evolution_record(
        path,
        {
            "run_id": "eval_fixture",
            "variant": "lexical",
            "metrics": {"recall_at_k": 0.5},
            "decision": "baseline",
        },
    )
    append_evolution_record(
        path,
        {
            "run_id": "eval_fixture_2",
            "variant": "hybrid",
            "metrics": {"recall_at_k": 0.75},
            "decision": "shadow",
        },
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["variant"] == "hybrid"
