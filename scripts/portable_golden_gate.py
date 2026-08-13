#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

from kip.evaluation.portable import portable_gate_failures, run_portable_gate

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evaluation/golden/production-regression.yaml"


def main() -> int:
    report = run_portable_gate(SUITE, project_root=ROOT)
    result = report["variants"]["lexical"]
    metrics = result["metrics"]
    p95 = float(result["latency_ms"]["p95"])
    failures = portable_gate_failures(report)
    print(
        "portable-golden-gate: "
        f"cases={metrics['case_count']} recall@k={metrics['recall_at_k']:.4f} "
        f"MRR={metrics['mrr']:.4f} unauthorized={metrics['unauthorized_result_count']} "
        f"p95={p95:.2f}ms"
    )
    if failures:
        print("portable-golden-gate: FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("portable-golden-gate: passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
