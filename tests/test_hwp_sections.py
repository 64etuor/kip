from __future__ import annotations

import zipfile
from importlib import import_module
from pathlib import Path


def _hwpx_section_xml(text: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        '<hp:p id="0" paraPrIDRef="0" styleIDRef="0">'
        f'<hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run>'
        "</hp:p></hs:sec>"
    )


def test_native_parser_labels_sections_numerically_despite_lexical_file_order(
    tmp_path: Path,
) -> None:
    # Given a real (not mocked) HWPX archive with 11 sections, so section10
    # sorts lexically before section2 in the dependency's own
    # _get_section_files() (a real ordering quirk this feature does not
    # attempt to fix - see hwp_native._reconstruct_section_spans).
    path = tmp_path / "eleven_sections.hwpx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/content.hpf", '<?xml version="1.0"?><package/>')
        for index in range(11):
            archive.writestr(f"Contents/section{index}.xml", _hwpx_section_xml(f"MARKER_{index}_END"))

    # When the section reconstruction runs through the real hwp-hwpx-parser
    # dependency directly (bypassing chunk splitting, whose windows can
    # straddle multiple small sections and would make a body-substring
    # assertion ambiguous).
    from kip.adapters.parsers import hwp_native as hwp_native_module

    reader_factory = import_module("hwp_hwpx_parser").Reader
    with reader_factory(path) as reader:
        text = reader.extract_text()
        spans = hwp_native_module._reconstruct_section_spans(reader, path, text)

    # Then reconstruction verified against extract_text() (spans is not
    # None) and each section's true numeric index - parsed from its
    # filename, never its position among the lexically-sorted files -
    # matches the marker embedded in that section's own text.
    assert spans is not None
    assert {span.section for span in spans} == set(range(11))
    for span in spans:
        assert f"MARKER_{span.section}_END" in text[span.start : span.end]
        assert f"MARKER_{span.section}_END" not in text[: span.start]
        assert f"MARKER_{span.section}_END" not in text[span.end :]


class _FakeHwp5Backend:
    def __init__(self, sections: dict[int, str]):
        self._sections = sections
        self.reset_calls = 0

    def _reset_counters(self):
        self.reset_calls += 1

    def _iter_sections(self):
        return iter(sorted(self._sections))

    def _read_section(self, section_idx):
        return section_idx

    def _extract_section_text(self, section_data, options):
        return self._sections[section_data]


class _FakeHwp5Reader:
    def __init__(self, backend: _FakeHwp5Backend):
        self._backend = backend

    def _get_reader(self):
        return self._backend


def test_reconstruct_section_spans_succeeds_for_hwp5_numeric_stream_order(
    tmp_path: Path,
) -> None:
    # Given a fake HWP5 backend whose _iter_sections() already yields the
    # real BodyText/SectionN order (0, 1, 2 - HWP5 has no lexical-sort bug,
    # unlike HWPX's _get_section_files()).
    from kip.adapters.parsers import hwp_native as hwp_native_module

    sections = {0: "ZERO", 1: "ONE", 2: "TWO"}
    backend = _FakeHwp5Backend(sections)
    reader = _FakeHwp5Reader(backend)
    full_text = "\n\n".join(sections[i] for i in sorted(sections))
    path = tmp_path / "fixture.hwp"

    # When the reconstruction runs.
    spans = hwp_native_module._reconstruct_section_spans(reader, path, full_text)

    # Then it verifies and labels every section with its real numeric index,
    # having reset counters first (mirroring HWP5Reader.extract_text()'s own
    # preamble) so re-numbered footnote/endnote markers would stay correct.
    assert backend.reset_calls == 1
    assert spans is not None
    assert [(span.section, full_text[span.start : span.end]) for span in spans] == [
        (0, "ZERO"),
        (1, "ONE"),
        (2, "TWO"),
    ]


def test_reconstruct_section_spans_falls_back_to_none_on_mismatch(
    tmp_path: Path,
) -> None:
    # Given a fake HWP5 backend whose per-section reconstruction does NOT
    # concatenate to the text the caller already extracted (simulating
    # dependency drift or an unexpected internal structure).
    from kip.adapters.parsers import hwp_native as hwp_native_module

    backend = _FakeHwp5Backend({0: "ZERO", 1: "ONE"})
    reader = _FakeHwp5Reader(backend)
    path = tmp_path / "fixture.hwp"

    # When the reconstruction is checked against text that cannot match.
    spans = hwp_native_module._reconstruct_section_spans(
        reader, path, "SOMETHING ENTIRELY DIFFERENT"
    )

    # Then it fails safe: no spans, so callers fall back to section: None
    # and a warning instead of ever emitting a wrong section number.
    assert spans is None


def test_reconstruct_section_spans_returns_none_for_unsupported_suffix(
    tmp_path: Path,
) -> None:
    # Given a path whose suffix is neither .hwp nor .hwpx.
    from kip.adapters.parsers import hwp_native as hwp_native_module

    backend = _FakeHwp5Backend({0: "ZERO"})
    reader = _FakeHwp5Reader(backend)
    path = tmp_path / "fixture.txt"

    # When reconstruction is attempted.
    spans = hwp_native_module._reconstruct_section_spans(reader, path, "ZERO")

    # Then it declines rather than guessing.
    assert spans is None


def test_section_for_offset_attributes_gaps_and_out_of_range_offsets() -> None:
    # Given section spans with a gap between them (the paragraph_separator).
    from kip.adapters.parsers.hwp_native import _section_for_offset, _SectionSpan

    spans = [_SectionSpan(section=0, start=0, end=4), _SectionSpan(section=1, start=6, end=10)]

    # When offsets land inside a span, inside the gap, and past the end.
    # Then interior offsets resolve to their own section, an offset inside
    # the gap attributes forward to the next section, and an offset past
    # every span falls back to the last section rather than raising.
    assert _section_for_offset(spans, 2) == 0
    assert _section_for_offset(spans, 5) == 1
    assert _section_for_offset(spans, 999) == 1
    assert _section_for_offset([], 0) is None
