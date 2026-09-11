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
import math
import os
import sys
from pathlib import Path

from kip.application.semantic import SEMANTIC_DEFAULT_MODE
from kip.container import build_container
from kip.domain.models import Capabilities, SearchHit, SearchRequest
from kip.errors import NotFoundError
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


def _variant_floors(floor: dict[str, object]) -> dict[str, dict[str, float]]:
    """Per-variant floors; the legacy single-``variant`` file is still read.

    Raises ``ValueError`` for a floor file that would otherwise gate less
    than it claims or crash mid-run (an empty ``variants`` object, a
    non-object variant, or a missing or non-numeric threshold).
    """
    keys = ("floor_recall_at_k", "floor_mrr", "max_failed_cases", "max_p95_ms")

    def thresholds(name: str, values: dict[str, object]) -> dict[str, float]:
        missing = [key for key in keys if key not in values]
        if missing:
            raise ValueError(f"floor file {name} lacks {', '.join(missing)}")
        checked: dict[str, float] = {}
        for key in keys:
            value = values[key]
            # bool is an int subclass; NaN would make every comparison pass;
            # a huge JSON integer overflows float conversion.
            try:
                finite = not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f"floor file {name} {key} must be a finite number")
            assert isinstance(value, int | float)
            upper = 1 if key.startswith("floor_") else None
            if value < 0 or (upper is not None and value > upper):
                bounds = "between 0 and 1" if upper is not None else "at least 0"
                raise ValueError(f"floor file {name} {key} must be {bounds}")
            checked[key] = value
        return checked

    if "variants" not in floor:
        if "variant" not in floor:
            raise ValueError("floor file lacks variant")
        return {str(floor["variant"]): thresholds("floor", floor)}
    variants = floor["variants"]
    if not isinstance(variants, dict) or not variants:
        raise ValueError("floor file `variants` must be a non-empty object")
    floors: dict[str, dict[str, float]] = {}
    for name, values in variants.items():
        if not isinstance(values, dict):
            raise ValueError(f"floor file variant {name} must be an object")
        floors[str(name)] = thresholds(f"variant {name}", values)
    return floors


def _gated_floors(
    floors: dict[str, dict[str, float]],
    settings: Settings,
    capabilities: Capabilities,
) -> dict[str, dict[str, float]] | None:
    """The floors this deployment must meet; None after reporting a failure."""
    default_mode = (
        str(settings.get("search.default_mode", SEMANTIC_DEFAULT_MODE))
        if capabilities.semantic_search_configured
        else "lexical"
    )
    if default_mode not in floors:
        # Gating only other variants would pass a deployment whose actual
        # default search path was never measured.
        print(
            f"golden-gate: FAILED - no floor for the configured default mode {default_mode}; "
            f"record one in {_FLOOR.relative_to(_ROOT)}"
        )
        return None
    semantic_floors = [variant for variant in floors if variant != "lexical"]
    if semantic_floors and capabilities.semantic_search_configured and not capabilities.semantic_search:
        # The corpus is here but the default semantic path is not: that is a
        # broken deployment, not a missing corpus, so it always fails.
        print(
            "golden-gate: FAILED\n  - semantic search is configured but the projection is "
            f"{capabilities.semantic_projection_status}; start the model runtime and run "
            "`kip projection rebuild --name semantic` or a sync before gating"
        )
        return None
    if not capabilities.semantic_search_configured:
        for variant in semantic_floors:
            print(f"golden-gate: {variant} floor not checked; semantic search is disabled in this deployment")
        return {variant: values for variant, values in floors.items() if variant == "lexical"}
    return floors


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
    # Any indexed corpus is not the reviewed one. A workspace holding only
    # sample or unrelated documents must skip (or fail closed when required)
    # instead of reporting a fake zero-recall regression.
    evidence_context = container.application.operations.request_context()
    expected = {document for case in dataset.cases for document in case.expected_documents}
    indexed = 0
    for document_id in sorted(expected):
        try:
            container.application.evidence.get_document(evidence_context, document_id)
        except NotFoundError:
            continue
        indexed += 1
    if indexed == 0:
        return _private_gate_unavailable("reviewed corpus is not indexed in this workspace")
    if indexed < len(expected):
        print(f"golden-gate: {len(expected) - indexed}/{len(expected)} expected documents are not indexed")
    try:
        floors = _variant_floors(json.loads(_FLOOR.read_text(encoding="utf-8")))
    except (ValueError, TypeError) as error:
        print(f"golden-gate: FAILED - {error}")
        return 1
    capabilities = container.application.operations.capabilities(evidence_context)
    gated = _gated_floors(floors, settings, capabilities)
    if gated is None:
        return 1
    floors = gated

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
        variants=list(floors),
        search=search_case,
        workspace=settings.workspace,
        dataset_bytes=_DATASET.read_bytes(),
        configuration=settings.raw,
        code_root=settings.project_root,
        warmup_passes=1,
    )
    failures: list[str] = []
    for variant, variant_floor in floors.items():
        result = report["variants"][variant]
        metrics = result["metrics"]
        recall = float(metrics["recall_at_k"])
        mrr = float(metrics["mrr"])
        failed = int(metrics["failed_case_count"])
        p95 = float(result["latency_ms"]["p95"])
        if recall < variant_floor["floor_recall_at_k"]:
            failures.append(f"{variant}: recall@k {recall:.4f} < floor {variant_floor['floor_recall_at_k']}")
        if mrr < variant_floor["floor_mrr"]:
            failures.append(f"{variant}: MRR {mrr:.4f} < floor {variant_floor['floor_mrr']}")
        if failed > variant_floor["max_failed_cases"]:
            failures.append(f"{variant}: failed cases {failed} > {variant_floor['max_failed_cases']}")
        if p95 > variant_floor["max_p95_ms"]:
            failures.append(f"{variant}: P95 {p95:.0f}ms > {variant_floor['max_p95_ms']}ms")
        print(
            f"golden-gate[{variant}]: recall@k={recall:.4f} MRR={mrr:.4f} "
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
