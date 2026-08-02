from __future__ import annotations

import pytest

from kip.evaluation.metrics import (
    deduplicate_ranked_documents,
    forbidden_document_count,
    locator_matches,
    ndcg_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank,
)


def test_document_metrics_deduplicate_units_before_ranking() -> None:
    ranked = deduplicate_ranked_documents(["doc_a", "doc_a", "doc_b", None, "doc_c"])

    assert ranked == ["doc_a", "doc_b", "doc_c"]
    assert recall_at_k(ranked, {"doc_a", "doc_b"}, 2) == 1.0
    assert reciprocal_rank(ranked, {"doc_b"}) == 0.5
    assert ndcg_at_k(ranked, {"doc_a", "doc_b"}, 3) == pytest.approx(1.0)


def test_metrics_handle_no_relevant_documents_or_hits() -> None:
    assert recall_at_k([], {"doc_a"}, 10) == 0.0
    assert reciprocal_rank([], {"doc_a"}) == 0.0
    assert ndcg_at_k([], {"doc_a"}, 10) == 0.0
    assert recall_at_k(["doc_a"], set(), 10) == 1.0


def test_forbidden_document_count_counts_unique_documents() -> None:
    assert forbidden_document_count(["doc_a", "doc_a", "doc_b"], {"doc_a", "doc_c"}) == 1


def test_locator_matches_type_and_partial_data() -> None:
    actual = {"type": "pdf_page", "data": {"page": 3, "bbox": [1, 2, 3, 4]}}

    assert locator_matches(actual, [{"type": "pdf_page", "data": {"page": 3}}])
    assert not locator_matches(actual, [{"type": "pdf_page", "data": {"page": 4}}])
    assert not locator_matches(actual, [{"type": "xlsx_range", "data": {}}])


def test_percentile_uses_linear_interpolation() -> None:
    assert percentile([7.0], 0.95) == 7.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == pytest.approx(3.85)
    assert percentile([], 0.95) == 0.0

