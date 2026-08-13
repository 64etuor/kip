#!/usr/bin/env python3
"""Fail if retrieval on the reviewed golden set regresses below its floor.

Runs the reviewed private golden dataset against the live corpus and
compares recall@k / MRR / failed-case count / P95 against a committed
floor file. Skips cleanly (exit 0) when no durable corpus is configured,
so it is safe to call from verify.sh in every environment while only
gating where the corpus actually exists.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from kip.container import build_container
from kip.domain.models import SearchHit, SearchRequest
from kip.evaluation.models import GoldenCase
from kip.evaluation.runner import load_dataset, run_evaluation
from kip.settings import Settings

_ROOT = Path(__file__).resolve().parents[1]
_DATASET = _ROOT / "evaluation" / "golden" / "private-onedrive-nl.yaml"
_FLOOR = _ROOT / "evaluation" / "golden" / "private-onedrive-nl.floor.json"


def _private_gate_unavailable(reason: str) -> int:
    required = os.environ.get("KIP_REQUIRE_PRIVATE_GOLDEN", "").casefold() in {
        "1",
        "true",
        "yes",
    }
    print(f"golden-gate: {reason}; {'FAILED' if required else 'skipping'}")
    return 1 if required else 0


def main() -> int:
    if not _DATASET.exists() or not _FLOOR.exists():
        return _private_gate_unavailable("reviewed dataset or floor missing")

    settings = Settings.load()
    if settings.database_url.startswith("memory://"):
        return _private_gate_unavailable("no durable corpus configured")

    container = build_container(settings)
    status = container.application.operations.status(
        container.application.operations.request_context()
    )
    if status.content_units == 0:
        return _private_gate_unavailable("durable corpus is empty")

    dataset = load_dataset(_DATASET)
    floor = json.loads(_FLOOR.read_text(encoding="utf-8"))

    def search_case(case: GoldenCase, variant: str) -> list[SearchHit]:
        context = container.application.operations.request_context(
            workspace=settings.workspace,
            principal_id=case.principal,
            acl_scopes=case.acl_scopes,
        )
        return container.application.retrieval.search(
            context,
            SearchRequest(query=case.question, limit=case.recall_at),
            mode=variant,
        )

    report = run_evaluation(
        dataset,
        variants=[floor["variant"]],
        search=search_case,
        workspace=settings.workspace,
        dataset_bytes=_DATASET.read_bytes(),
        configuration=settings.raw,
        code_root=settings.project_root,
        warmup_passes=1,
    )
    result = report["variants"][floor["variant"]]
    metrics = result["metrics"]
    recall = float(metrics["recall_at_k"])
    mrr = float(metrics["mrr"])
    failed = int(metrics["failed_case_count"])
    p95 = float(result["latency_ms"]["p95"])

    failures: list[str] = []
    if recall < floor["floor_recall_at_k"]:
        failures.append(f"recall@k {recall:.4f} < floor {floor['floor_recall_at_k']}")
    if mrr < floor["floor_mrr"]:
        failures.append(f"MRR {mrr:.4f} < floor {floor['floor_mrr']}")
    if failed > floor["max_failed_cases"]:
        failures.append(f"failed cases {failed} > {floor['max_failed_cases']}")
    if p95 > floor["max_p95_ms"]:
        failures.append(f"P95 {p95:.0f}ms > {floor['max_p95_ms']}ms")

    print(
        f"golden-gate: recall@k={recall:.4f} MRR={mrr:.4f} "
        f"failed={failed} p95={p95:.0f}ms"
    )
    if failures:
        print("golden-gate: FAILED")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("golden-gate: passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
