from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from kip.domain.models import EvidenceLocator, SearchHit
from kip.evaluation.runner import compare_variants, load_dataset, run_evaluation


def _write_dataset(path: Path, *, duplicate: bool = False) -> None:
    second_id = "GQ-001" if duplicate else "GQ-002"
    path.write_text(
        f"""
schema_version: kip.golden-dataset.v1
name: fixture
corpus_fingerprint: sha256:fixture
cases:
  - id: GQ-001
    question: 참여율 변경 승인
    category: exact_identifier
    principal: principal_public
    acl_scopes: [workspace:default, public]
    expected_documents: [doc_a]
    forbidden_documents: [doc_secret]
    expected_evidence:
      - type: text_span
        data:
          line_start: 1
    recall_at: 10
  - id: {second_id}
    question: 허가되지 않은 내부 자료
    category: access_denied
    principal: principal_public
    acl_scopes: [workspace:default, public]
    expected_documents: []
    forbidden_documents: [doc_secret]
    recall_at: 10
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _hit(document_id: str, unit_id: str = "unit_a") -> SearchHit:
    return SearchHit(
        unit_id=unit_id,
        document_id=document_id,
        artifact_id="art_a",
        source_kind="filesystem",
        title="승인 공문",
        snippet="참여율 변경을 승인한다.",
        score=10.0,
        locator=EvidenceLocator(type="text_span", data={"line_start": 1, "line_end": 2}),
        source_uri="file:///public/approval.txt",
        source_sha256="a" * 64,
    )


def test_load_dataset_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    dataset = tmp_path / "duplicate.yaml"
    _write_dataset(dataset, duplicate=True)

    with pytest.raises(ValueError, match="unique"):
        load_dataset(dataset)


def test_run_evaluation_returns_required_metrics_and_redacted_cases(tmp_path: Path) -> None:
    dataset_path = tmp_path / "golden.yaml"
    _write_dataset(dataset_path)

    def search(case, variant):
        assert variant == "lexical"
        return [_hit("doc_a")] if case.id == "GQ-001" else []

    report = run_evaluation(
        load_dataset(dataset_path),
        variants=["lexical"],
        search=search,
        workspace="default",
        dataset_bytes=dataset_path.read_bytes(),
        configuration={"search": {"semantic_enabled": False}},
        code_root=tmp_path,
        now=lambda: datetime(2026, 7, 30, tzinfo=UTC),
    )

    metrics = report["variants"]["lexical"]["metrics"]
    assert metrics["case_count"] == 2
    assert metrics["recall_at_k"] == 1.0
    assert metrics["unauthorized_result_count"] == 0
    assert metrics["locator_accuracy"] == 1.0
    assert report["fingerprints"]["dataset"].startswith("sha256:")
    assert "참여율 변경을 승인한다" not in str(report)


def test_run_evaluation_excludes_declared_warmup_passes_from_metrics(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "golden.yaml"
    _write_dataset(dataset_path)
    calls: list[tuple[str, str]] = []

    def search(case, variant):
        calls.append((case.id, variant))
        return [_hit("doc_a")] if case.id == "GQ-001" else []

    report = run_evaluation(
        load_dataset(dataset_path),
        variants=["lexical"],
        search=search,
        workspace="default",
        dataset_bytes=dataset_path.read_bytes(),
        configuration={},
        code_root=tmp_path,
        warmup_passes=1,
        now=lambda: datetime(2026, 7, 30, tzinfo=UTC),
    )

    assert calls == [
        ("GQ-001", "lexical"),
        ("GQ-002", "lexical"),
        ("GQ-001", "lexical"),
        ("GQ-002", "lexical"),
    ]
    assert report["run"]["warmup_passes"] == 1
    assert report["variants"]["lexical"]["metrics"]["case_count"] == 2


def test_compare_variants_applies_activation_gate() -> None:
    report = {
        "variants": {
            "lexical": {
                "metrics": {"recall_at_k": 0.80, "unauthorized_result_count": 0},
                "latency_ms": {"p95": 100.0},
                "categories": {"exact_identifier": {"recall_at_k": 1.0}},
            },
            "reranked": {
                "metrics": {"recall_at_k": 0.85, "unauthorized_result_count": 0},
                "latency_ms": {"p95": 500.0},
                "categories": {"exact_identifier": {"recall_at_k": 1.0}},
            },
        }
    }

    result = compare_variants(report, "lexical", "reranked")

    assert result["decision"]["status"] == "promote"
    assert result["gates"]["overall_recall_improvement"]["passed"] is True
