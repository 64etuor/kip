from __future__ import annotations

from kip.adapters.analyzers.korean_ngram import KoreanNgramAnalyzer
from kip.domain.text import normalize_text


def test_analyze_indexes_hangul_before_trailing_punctuation() -> None:
    # Given
    analyzer = KoreanNgramAnalyzer()

    # When
    tokens = analyzer.analyze("변경은 승인한다.").split()

    # Then
    assert "승인" in tokens


def test_normalize_text_preserves_reference_behavior() -> None:
    # Given
    raw = "Ａ과제\x00  참여율\n변경"

    # When
    normalized = normalize_text(raw)

    # Then
    assert normalized == "A과제 참여율 변경"
