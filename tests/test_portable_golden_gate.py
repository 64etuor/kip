from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from kip.evaluation.portable import (
    HashingEmbedding,
    expand_portable_dataset,
    load_portable_suite,
    portable_gate_failures,
    run_portable_gate,
    shipped_search_settings,
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


def test_portable_gate_also_runs_the_shipped_semantic_default_mode() -> None:
    # Given the shipped configuration turns semantic search on by default.
    shipped = shipped_search_settings(ROOT)
    assert shipped["search"]["semantic_enabled"] is True
    default_mode = shipped["search"]["default_mode"]

    # When the portable gate runs without a model runtime (hosted CI).
    report = run_portable_gate(SUITE, project_root=ROOT)

    # Then the default pipeline, with a deterministic embedding, meets the same bar.
    assert set(report["variants"]) == {"lexical", default_mode}
    metrics = report["variants"][default_mode]["metrics"]
    assert (metrics["recall_at_k"], metrics["mrr"]) == (1.0, 1.0)
    assert metrics["unauthorized_result_count"] == 0


def test_portable_gate_failures_name_the_failing_variant() -> None:
    passing = {"case_count": 120, "recall_at_k": 1.0, "mrr": 1.0, "failed_case_count": 0, "unauthorized_result_count": 0}
    report = {
        "variants": {
            "lexical": {"metrics": passing, "latency_ms": {"p95": 1.0}},
            "reranked": {"metrics": {**passing, "unauthorized_result_count": 1}, "latency_ms": {"p95": 1.0}},
        }
    }
    assert portable_gate_failures(report) == ["reranked: an ACL-forbidden document was returned"]


def test_hashing_embedding_is_deterministic_and_normalized() -> None:
    embedding = HashingEmbedding()
    first = embedding.embed_query("협약 변경 승인 절차")
    assert first == embedding.embed_documents(["협약 변경 승인 절차"])[0]
    assert len(first) == embedding.dimensions
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9


def test_private_gate_reads_legacy_and_per_variant_floors() -> None:
    namespace = runpy.run_path(str(ROOT / "scripts/golden_gate.py"), run_name="golden_gate_test")
    variant_floors = namespace["_variant_floors"]
    legacy = {"variant": "lexical", "floor_recall_at_k": 0.7, "floor_mrr": 0.6, "max_failed_cases": 0, "max_p95_ms": 100}
    assert variant_floors(legacy) == {
        "lexical": {"floor_recall_at_k": 0.7, "floor_mrr": 0.6, "max_failed_cases": 0, "max_p95_ms": 100}
    }
    current = json.loads((ROOT / "evaluation/golden/private-onedrive-nl.floor.json").read_text(encoding="utf-8"))
    floors = variant_floors(current)
    assert "lexical" in floors
    assert shipped_search_settings(ROOT)["search"]["default_mode"] in floors


def test_required_private_gate_fails_closed_without_required_evidence() -> None:
    # Given a protected-runner policy that requires private corpus evidence.
    environment = {
        **os.environ,
        "KIP_DATABASE_URL": "memory://",
        "KIP_REQUIRE_PRIVATE_GOLDEN": "1",
    }

    # When the private files are absent or the configured repository is ephemeral.
    result = subprocess.run(
        [sys.executable, "scripts/golden_gate.py"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then the first missing requirement blocks promotion instead of passing.
    assert result.returncode == 1
    assert "FAILED" in result.stdout
    private_dataset = ROOT / "evaluation/golden/private-onedrive-nl.yaml"
    private_floor = ROOT / "evaluation/golden/private-onedrive-nl.floor.json"
    expected_reason = (
        "no durable corpus configured"
        if private_dataset.exists() and private_floor.exists()
        else "reviewed dataset or floor missing"
    )
    assert expected_reason in result.stdout


def _private_gate_main(monkeypatch, tmp_path: Path, *, search: dict, floor: dict, capabilities: dict):
    """The private gate's `main` wired to a fake durable corpus."""
    from types import SimpleNamespace

    from kip.domain.models import Capabilities
    from kip.settings import Settings

    main = runpy.run_path(str(ROOT / "scripts/golden_gate.py"), run_name="golden_gate_test")["main"]
    dataset_path = tmp_path / "dataset.yaml"
    dataset_path.write_text("cases: []\n", encoding="utf-8")
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={"search": search},
        database_url="postgresql://gate",
    )
    operations = SimpleNamespace(
        request_context=lambda **_kwargs: SimpleNamespace(),
        status=lambda _context: SimpleNamespace(content_units=10),
        capabilities=lambda _context: Capabilities(
            repository="postgres",
            lexical_search=True,
            graph_backend="postgres",
            api=True,
            mcp=True,
            parsers={},
            connectors={},
            **capabilities,
        ),
    )
    container = SimpleNamespace(
        application=SimpleNamespace(
            operations=operations,
            evidence=SimpleNamespace(get_document=lambda _context, document_id: document_id),
        )
    )
    namespace = main.__globals__
    monkeypatch.setitem(namespace, "_DATASET", dataset_path)
    monkeypatch.setitem(namespace, "Settings", SimpleNamespace(load=lambda: settings))
    monkeypatch.setitem(namespace, "build_container", lambda _settings: container)
    monkeypatch.setitem(
        namespace,
        "load_dataset",
        lambda _path: SimpleNamespace(cases=[SimpleNamespace(expected_documents=["doc_1"])]),
    )
    written = tmp_path / "floor.json"
    written.write_text(json.dumps(floor), encoding="utf-8")
    monkeypatch.setitem(namespace, "_FLOOR", written)
    monkeypatch.setitem(namespace, "_ROOT", tmp_path)
    return main


_THRESHOLDS = {"floor_recall_at_k": 0.8, "floor_mrr": 0.6, "max_failed_cases": 0, "max_p95_ms": 8000}


def test_private_gate_fails_when_semantic_is_configured_but_not_ready(monkeypatch, tmp_path: Path, capsys) -> None:
    main = _private_gate_main(
        monkeypatch,
        tmp_path,
        search={"semantic_enabled": True, "default_mode": "hybrid"},
        floor={"variants": {"hybrid": _THRESHOLDS, "lexical": _THRESHOLDS}},
        capabilities={
            "semantic_search": False,
            "semantic_search_configured": True,
            "semantic_projection_status": "missing",
        },
    )

    assert main() == 1
    output = capsys.readouterr().out
    assert "golden-gate: FAILED" in output and "projection is missing" in output


def test_private_gate_requires_a_floor_for_the_configured_default_mode(monkeypatch, tmp_path: Path, capsys) -> None:
    ready = {"semantic_search": True, "semantic_search_configured": True, "semantic_projection_status": "active"}
    main = _private_gate_main(
        monkeypatch,
        tmp_path,
        search={"semantic_enabled": True, "default_mode": "reranked"},
        floor={"variants": {"hybrid": _THRESHOLDS, "lexical": _THRESHOLDS}},
        capabilities=ready,
    )
    assert main() == 1
    assert (
        "golden-gate: FAILED - no floor for the configured default mode reranked; record one in floor.json"
        in capsys.readouterr().out
    )

    disabled = {"semantic_search": False, "semantic_search_configured": False}
    main = _private_gate_main(
        monkeypatch,
        tmp_path,
        search={"semantic_enabled": False},
        floor={"variants": {"hybrid": _THRESHOLDS}},
        capabilities=disabled,
    )
    assert main() == 1
    assert "no floor for the configured default mode lexical" in capsys.readouterr().out


def test_private_gate_rejects_empty_or_malformed_variant_floors(monkeypatch, tmp_path: Path, capsys) -> None:
    variant_floors = runpy.run_path(str(ROOT / "scripts/golden_gate.py"), run_name="golden_gate_test")[
        "_variant_floors"
    ]
    with pytest.raises(ValueError, match="non-empty object"):
        variant_floors({"variants": {}})
    with pytest.raises(ValueError, match="variant hybrid must be an object"):
        variant_floors({"variants": {"hybrid": 0.8, "lexical": _THRESHOLDS}})
    with pytest.raises(ValueError, match="variant lexical lacks floor_mrr"):
        variant_floors({"variants": {"lexical": {key: value for key, value in _THRESHOLDS.items() if key != "floor_mrr"}}})

    main = _private_gate_main(
        monkeypatch,
        tmp_path,
        search={"semantic_enabled": False},
        floor={"variants": {"lexical": [0.8]}},
        capabilities={"semantic_search": False},
    )
    assert main() == 1
    assert "golden-gate: FAILED - floor file variant lexical must be an object" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["0.8", None, True, float("nan"), float("inf"), 10**400])
def test_private_gate_rejects_non_numeric_floor_thresholds(bad) -> None:
    variant_floors = runpy.run_path(str(ROOT / "scripts/golden_gate.py"), run_name="golden_gate_test")[
        "_variant_floors"
    ]
    values = {"floor_recall_at_k": 0.8, "floor_mrr": bad, "max_failed_cases": 0, "max_p95_ms": 8000}
    with pytest.raises(ValueError, match="finite number"):
        variant_floors({"variants": {"hybrid": values}})


@pytest.mark.parametrize(("key", "bad"), [("floor_recall_at_k", -1), ("floor_mrr", 1.5), ("max_p95_ms", -5)])
def test_private_gate_rejects_out_of_range_floor_thresholds(key: str, bad: float) -> None:
    variant_floors = runpy.run_path(str(ROOT / "scripts/golden_gate.py"), run_name="golden_gate_test")[
        "_variant_floors"
    ]
    values = {"floor_recall_at_k": 0.8, "floor_mrr": 0.6, "max_failed_cases": 0, "max_p95_ms": 8000, key: bad}
    with pytest.raises(ValueError, match="must be"):
        variant_floors({"variants": {"hybrid": values}})
