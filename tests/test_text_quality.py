from __future__ import annotations

from kip.adapters.parsers.text_quality import (
    hangul_ratio,
    hwp_text_quality,
    printable_ratio,
    replacement_ratio,
)


def test_ratios_are_zero_for_empty_text() -> None:
    # Given no text at all.
    # When each ratio helper scores it.
    # Then there is nothing to credit or penalize, so every ratio is zero.
    assert printable_ratio("") == 0.0
    assert hangul_ratio("") == 0.0
    assert replacement_ratio("") == 0.0


def test_printable_ratio_counts_newline_and_tab_as_printable() -> None:
    text = "a\nb\tc"

    assert printable_ratio(text) == 1.0


def test_printable_ratio_penalizes_control_characters() -> None:
    text = "ab\x00\x01"

    assert printable_ratio(text) == 0.5


def test_hangul_ratio_measures_hangul_syllable_density() -> None:
    # Given a mix of two Hangul syllables and two ASCII characters.
    text = "가나ab"

    assert hangul_ratio(text) == 0.5


def test_hangul_ratio_is_zero_without_any_hangul() -> None:
    assert hangul_ratio("hello world") == 0.0


def test_replacement_ratio_measures_decode_failure_markers() -> None:
    text = "��ab"

    assert replacement_ratio(text) == 0.5


def test_replacement_ratio_is_zero_for_clean_text() -> None:
    assert replacement_ratio("정상 텍스트") == 0.0


def test_hwp_text_quality_is_zero_for_empty_text() -> None:
    assert hwp_text_quality("") == 0.0


def test_hwp_text_quality_rewards_long_clean_hangul_text() -> None:
    text = "정상적으로 추출된 계약 문서 본문입니다. " * 100

    assert hwp_text_quality(text) >= 0.9


def test_hwp_text_quality_penalizes_short_low_hangul_text() -> None:
    assert hwp_text_quality("abc") < hwp_text_quality("정상적으로 추출된 계약 문서 본문입니다. " * 100)


def test_hwp_text_quality_penalizes_parser_warnings() -> None:
    text = "정상적으로 추출된 계약 문서 본문입니다. " * 100

    without_warnings = hwp_text_quality(text, warning_count=0)
    with_warnings = hwp_text_quality(text, warning_count=5)

    assert with_warnings < without_warnings


def test_hwp_text_quality_negative_warning_count_does_not_increase_score() -> None:
    # Given a negative warning_count (a caller bug, e.g. a delta instead of
    # a total), which would otherwise make warning_penalty negative and
    # subtracting it *raise* the score above the same text's zero-warning
    # baseline.
    text = "정상적으로 추출된 계약 문서 본문입니다. " * 100

    baseline = hwp_text_quality(text, warning_count=0)
    negative = hwp_text_quality(text, warning_count=-5)

    assert negative <= baseline


def test_hwp_text_quality_stays_bounded_to_zero_and_one() -> None:
    huge = "가" * 100_000

    assert 0.0 <= hwp_text_quality(huge) <= 1.0
