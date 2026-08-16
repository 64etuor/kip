from __future__ import annotations

import json
import sys
from pathlib import Path

from kip.adapters.parsers.hwp_broker import CommandParserConfig, HwpParserBroker


def test_command_broker_preserves_kordoc_structured_blocks(tmp_path: Path) -> None:
    # Given a Kordoc-compatible payload containing a table, an image, and a typed warning.
    payload = {
        "metadata": {"parserVersion": "4.7.3"},
        "blocks": [
            {
                "type": "paragraph",
                "text": "승인 완료",
                "pageNumber": 1,
                "spans": [{"text": "승인", "bold": True}, {"text": " 완료"}],
                "footnoteText": "결재번호 A-1",
            },
            {
                "type": "table",
                "pageNumber": 2,
                "table": {
                    "rows": 2,
                    "cols": 2,
                    "hasHeader": True,
                    "cells": [
                        [
                            {"text": "항목", "rowSpan": 1, "colSpan": 1, "isHeader": True},
                            {"text": "값", "rowSpan": 1, "colSpan": 1, "isHeader": True},
                        ],
                        [
                            {"text": "일정", "rowSpan": 1, "colSpan": 1},
                            {"text": "2026-08-13", "rowSpan": 1, "colSpan": 1},
                        ],
                    ],
                },
            },
            {
                "type": "image",
                "pageNumber": 2,
                "imageData": {"filename": "도면.png", "mimeType": "image/png"},
            },
        ],
        "warnings": [{"page": 2, "code": "PARTIAL_PARSE", "message": "도형 일부를 건너뜀"}],
    }
    source = tmp_path / "fixture.hwp"
    source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"fixture")
    command = [sys.executable, "-c", f"print({json.dumps(json.dumps(payload))})"]
    broker = HwpParserBroker([CommandParserConfig(name="kordoc", argv=command)])

    # When the command boundary translates the structured payload.
    extraction, units = broker.parse(
        source,
        artifact_id="art_fixture",
        document_id="doc_fixture",
        acl_scopes=["workspace:default"],
    )

    # Then no meaningful block is dropped and machine-readable structure remains available.
    assert [unit.unit_type for unit in units] == ["paragraph", "table", "image"]
    assert units[1].body == "항목\t값\n일정\t2026-08-13"
    assert units[1].metadata["table"]["cells"][1][1]["text"] == "2026-08-13"
    assert units[2].body == "[image: 도면.png]"
    assert units[2].metadata["image"] == {
        "filename": "도면.png",
        "mime_type": "image/png",
    }
    assert extraction.warnings == ["PARTIAL_PARSE page 2: 도형 일부를 건너뜀"]
    # And the locator never trusts kordoc's never-emitted section/sectionNumber
    # keys, and pairs every non-null page with a page_mode so a reader can
    # tell a real page from a section-position approximation (ADR-049).
    assert all(unit.locator.data["section"] is None for unit in units)
    assert [unit.locator.data["page"] for unit in units] == [1, 2, 2]
    assert [unit.locator.data["page_mode"] for unit in units] == [
        "section_approx",
        "section_approx",
        "section_approx",
    ]


def test_command_broker_marks_page_exact_only_when_metadata_says_so(
    tmp_path: Path,
) -> None:
    # Given a payload whose document metadata explicitly claims exact pages.
    payload = {
        "metadata": {"parserVersion": "4.7.3", "pageMode": "exact"},
        "blocks": [{"type": "paragraph", "text": "승인 완료", "pageNumber": 1}],
    }
    source = tmp_path / "fixture.hwp"
    source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"fixture")
    command = [sys.executable, "-c", f"print({json.dumps(json.dumps(payload))})"]
    broker = HwpParserBroker([CommandParserConfig(name="kordoc", argv=command)])

    # When the command boundary translates the payload.
    _extraction, units = broker.parse(
        source,
        artifact_id="art_fixture",
        document_id="doc_fixture",
        acl_scopes=["workspace:default"],
    )

    # Then the locator records the exact page mode instead of the
    # conservative section_approx default.
    assert units[0].locator.data["page"] == 1
    assert units[0].locator.data["page_mode"] == "exact"


def test_command_broker_leaves_page_mode_null_when_no_page_is_placed(
    tmp_path: Path,
) -> None:
    # Given a block that carries no pageNumber at all.
    payload = {
        "metadata": {"parserVersion": "4.7.3"},
        "blocks": [{"type": "paragraph", "text": "승인 완료"}],
    }
    source = tmp_path / "fixture.hwp"
    source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"fixture")
    command = [sys.executable, "-c", f"print({json.dumps(json.dumps(payload))})"]
    broker = HwpParserBroker([CommandParserConfig(name="kordoc", argv=command)])

    # When the command boundary translates the payload.
    _extraction, units = broker.parse(
        source,
        artifact_id="art_fixture",
        document_id="doc_fixture",
        acl_scopes=["workspace:default"],
    )

    # Then page_mode is never fabricated for an absent page.
    assert units[0].locator.data["page"] is None
    assert units[0].locator.data["page_mode"] is None
