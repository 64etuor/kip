from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from kip.evaluation.portable import (
    expand_portable_dataset,
    load_portable_suite,
    portable_gate_failures,
    run_portable_gate,
)

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evaluation/golden/production-regression.yaml"


def test_portable_production_suite_has_at_least_one_hundred_reviewed_cases() -> None:
    # Given the source-controlled portable production suite.
    suite = load_portable_suite(SUITE)

    # When compact document scenarios are expanded into immutable cases.
    dataset = expand_portable_dataset(suite, SUITE.read_bytes())

    # Then CI evaluates broad positive and ACL-negative coverage.
    assert len(dataset.cases) >= 100
    assert dataset.gate_eligible is True
    assert {case.category for case in dataset.cases} >= {
        "exact_identifier",
        "natural_language",
        "code_switch",
        "typo_noise",
        "access_denied",
    }


def test_portable_production_gate_passes_the_real_application_pipeline() -> None:
    # Given a deterministic corpus loaded through the normal repository contract.
    report = run_portable_gate(SUITE, project_root=ROOT)

    # When the fixed lexical pipeline is evaluated over all expanded cases.
    result = report["variants"]["lexical"]
    metrics = result["metrics"]

    # Then every expected document is found and no ACL-forbidden document leaks.
    assert metrics["case_count"] >= 100
    assert metrics["recall_at_k"] == 1.0
    assert metrics["mrr"] == 1.0
    assert metrics["failed_case_count"] == 0
    assert metrics["unauthorized_result_count"] == 0
    assert portable_gate_failures(report) == []


def test_required_private_gate_fails_closed_without_a_durable_corpus() -> None:
    # Given a protected-runner policy that requires private corpus evidence.
    environment = {
        **os.environ,
        "KIP_DATABASE_URL": "memory://",
        "KIP_REQUIRE_PRIVATE_GOLDEN": "1",
    }

    # When the private gate cannot reach a durable corpus.
    result = subprocess.run(
        [sys.executable, "scripts/golden_gate.py"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then missing evidence blocks promotion instead of silently passing.
    assert result.returncode == 1
    assert "FAILED" in result.stdout
    assert "durable corpus" in result.stdout
