from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ReportPaths:
    json_path: Path
    markdown_path: Path


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _percentage(value: object) -> str:
    if not isinstance(value, int | float):
        return "n/a"
    return f"{float(value) * 100:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    run = report["run"]
    lines = [
        "# KIP RAG Evaluation",
        "",
        f"- Run: `{run['id']}`",
        f"- Dataset: `{run.get('dataset', 'unknown')}`",
        f"- Dataset version: `{run.get('dataset_version', 'draft')}`",
        f"- Dataset lifecycle: `{run.get('dataset_lifecycle', 'draft')}`",
        f"- Promotion eligible: `{run.get('dataset_gate_eligible', False)}`",
        f"- Workspace: `{run.get('workspace', 'unknown')}`",
        f"- Completed: `{run['completed_at']}`",
        f"- Untimed warmup passes: `{run.get('warmup_passes', 0)}`",
        "",
        "## Variant scorecard",
        "",
        "| Variant | Cases | Failures | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, result in report["variants"].items():
        metrics = result["metrics"]
        latency = result["latency_ms"]
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    str(metrics["case_count"]),
                    str(metrics.get("failed_case_count", 0)),
                    _percentage(metrics["recall_at_k"]),
                    _percentage(metrics["mrr"]),
                    _percentage(metrics["ndcg_at_k"]),
                    str(metrics["unauthorized_result_count"]),
                    f"{latency['p50']:.2f}",
                    f"{latency['p95']:.2f}",
                ]
            )
            + " |"
        )
    if any("answer_quality" in result for result in report["variants"].values()):
        lines.extend(
            [
                "",
                "## Answer quality",
                "",
                "| Variant | Reviewed | Claim P | Claim R | Citation P | Citation R | Refusal | Unsupported |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for name, result in report["variants"].items():
            quality = result.get("answer_quality")
            if quality is None:
                continue
            metrics = quality["metrics"]
            lines.append(
                "| "
                + " | ".join(
                    [
                        name,
                        str(metrics["case_count"]),
                        _percentage(metrics["claim_precision"]),
                        _percentage(metrics["claim_recall"]),
                        _percentage(metrics["citation_precision"]),
                        _percentage(metrics["citation_recall"]),
                        _percentage(metrics["refusal_appropriateness"]),
                        str(metrics["unsupported_claim_count"]),
                    ]
                )
                + " |"
            )
    if any("ontology_quality" in result for result in report["variants"].values()):
        lines.extend(
            [
                "",
                "## Ontology quality",
                "",
                "| Variant | Reviewed | Entity P/R | Relation P/R | Evidence P/R | Path P/R | Temporal | Duplicates | Orphans | ACL leaks |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for name, result in report["variants"].items():
            quality = result.get("ontology_quality")
            if quality is None:
                continue
            metrics = quality["metrics"]
            lines.append(
                "| "
                + " | ".join(
                    [
                        name,
                        str(metrics["case_count"]),
                        f"{_percentage(metrics['entity_precision'])}/{_percentage(metrics['entity_recall'])}",
                        f"{_percentage(metrics['relation_precision'])}/{_percentage(metrics['relation_recall'])}",
                        f"{_percentage(metrics['evidence_precision'])}/{_percentage(metrics['evidence_recall'])}",
                        f"{_percentage(metrics['path_relevance'])}/{_percentage(metrics['path_recall'])}",
                        _percentage(metrics["temporal_accuracy"]),
                        str(metrics["duplicate_count"]),
                        str(metrics["orphan_count"]),
                        str(metrics["acl_leakage_count"]),
                    ]
                )
                + " |"
            )
    decision = report["decision"]
    lines.extend(["", "## Decision", "", f"Status: **{decision['status']}**"])
    reasons = decision.get("reasons") or []
    if reasons:
        lines.extend(["", *[f"- {reason}" for reason in reasons]])
    lines.extend(
        [
            "",
            "## Reproducibility fingerprints",
            "",
            *[
                f"- {name}: `{value}`"
                for name, value in report["fingerprints"].items()
                if isinstance(value, str)
            ],
            "",
        ]
    )
    return "\n".join(lines)


def write_report(report: dict[str, Any], output_dir: Path) -> ReportPaths:
    run_id = str(report["run"]["id"])
    if not run_id or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        for character in run_id
    ):
        raise ValueError("run id contains unsafe characters")
    json_data = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    markdown_data = render_markdown(report).encode()
    json_path = output_dir / f"{run_id}.json"
    markdown_path = output_dir / f"{run_id}.md"
    _atomic_write(json_path, json_data)
    _atomic_write(markdown_path, markdown_data)
    _atomic_write(output_dir / "latest.json", json_data)
    _atomic_write(output_dir / "latest.md", markdown_data)
    return ReportPaths(json_path=json_path, markdown_path=markdown_path)


def append_evolution_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
