from __future__ import annotations

from kip.adapters.repository.postgres.database import _websearch_or_query


def test_short_queries_keep_every_term() -> None:
    query = _websearch_or_query("품질 검사 품질검사")

    assert query == '"품질" OR "검사" OR "품질검사"'


def test_long_queries_keep_the_most_selective_terms() -> None:
    grams = [f"g{index:02d}" for index in range(70)]  # 3-char terms
    words = ["아주긴선택적단어", "협력업체평가"]
    query = _websearch_or_query(" ".join(grams + words), max_terms=10)

    terms = [part.strip('"') for part in query.split(" OR ")]
    assert len(terms) == 10
    assert terms[0] == "아주긴선택적단어"
    assert terms[1] == "협력업체평가"
    # Remaining slots fall back to the earliest same-length grams, stably.
    assert terms[2:] == grams[:8]


def test_duplicate_terms_are_removed_before_the_cap() -> None:
    query = _websearch_or_query("중복 중복 중복 유일")

    assert query == '"중복" OR "유일"'
