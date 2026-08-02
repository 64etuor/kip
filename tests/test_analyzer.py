from __future__ import annotations

from kip.application.analyzer import KoreanNgramAnalyzer


def test_analyze_indexes_hangul_before_trailing_punctuation() -> None:
    # Given
    analyzer = KoreanNgramAnalyzer()

    # When
    tokens = analyzer.analyze("변경은 승인한다.").split()

    # Then
    assert "승인" in tokens
