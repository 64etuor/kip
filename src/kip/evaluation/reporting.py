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
        "# KIP Retrieval Evaluation",
        "",
        f"- Run: `{run['id']}`",
        f"- Dataset: `{run.get('dataset', 'unknown')}`",
        f"- Workspace: `{run.get('workspace', 'unknown')}`",
        f"- Completed: `{run['completed_at']}`",
        f"- Untimed warmup passes: `{run.get('warmup_passes', 0)}`",
        "",
        "## Variant scorecard",
        "",
        "| Variant | Cases | Recall@K | MRR | nDCG@K | Unauthorized | P50 ms | P95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
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
    if not run_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in run_id):
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
