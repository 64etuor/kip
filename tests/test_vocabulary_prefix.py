from __future__ import annotations

import pytest

from kip.errors import ValidationError


def _seed(test_container) -> None:
    source = test_container.settings.project_root / "source"
    (source / "정산문서.txt").write_text(
        "정산 정산서 정산기준 행정 절차", encoding="utf-8"
    )
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")


def test_vocabulary_matches_by_prefix_not_substring(test_container):
    _seed(test_container)
    context = test_container.application.operations.request_context()

    terms = {
        item.term
        for item in test_container.application.retrieval.vocabulary(context, "정산", 20)
    }

    assert any(term.startswith("정산") for term in terms)
    # 행정 contains "정" but does not start with the prefix.
    assert all(term.startswith("정산") for term in terms)


def test_blank_and_multiword_prefixes_are_rejected(test_container):
    _seed(test_container)
    context = test_container.application.operations.request_context()

    with pytest.raises(ValidationError, match="must not be blank"):
        test_container.application.retrieval.vocabulary(context, "   ", 20)
    with pytest.raises(ValidationError, match="single term"):
        test_container.application.retrieval.vocabulary(context, "정산 기준", 20)
