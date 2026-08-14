from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kip.adapters.ocr.kordoc import KordocOcrAdapter, KordocOcrConfig
from kip.adapters.parsers.pdf import PdfParser
from kip.adapters.parsers.registry import ParserRegistry
from kip.errors import ConfigurationError, ParserError
from kip.settings import Settings


def test_kordoc_ocr_preserves_korean_block_locator(tmp_path: Path) -> None:
    # Given a command that returns one Kordoc OCR document.
    command = tmp_path / "fake_kordoc.py"
    command.write_text(
        """
import json
print(json.dumps({
    "success": True,
    "fileType": "image",
    "blocks": [
        {
            "type": "paragraph",
            "text": "공정 데이터 관리 시스템",
            "pageNumber": 1,
            "bbox": {"x": 10, "y": 20, "width": 300, "height": 40},
            "style": {"align": "center"}
        },
        {
            "type": "table",
            "pageNumber": 1,
            "table": {"cells": [[{"text": "점검 항목"}, {"text": "완료"}]]}
        },
        {"type": "image", "text": "image_001.png", "pageNumber": 1}
    ],
    "metadata": {"ocrEngine": "PP-OCRv5 korean"},
    "warnings": [
        {"code": "OCR_APPLIED", "message": "OCR 적용"},
        {"code": "OCR_LOW_CONF", "page": 1, "message": "저신뢰 라인 폐기"}
    ]
}, ensure_ascii=False))
""".strip(),
        encoding="utf-8",
    )
    image = tmp_path / "slide.png"
    image.write_bytes(b"fixture")
    adapter = KordocOcrAdapter(
        KordocOcrConfig(argv=(sys.executable, str(command)), timeout_seconds=5)
    )

    # When the image crosses the OCR command boundary.
    documents = adapter.recognize((image,))

    # Then Korean text and its page/bounding-box evidence survive validation.
    assert documents[0].blocks[0].text == "공정 데이터 관리 시스템"
    assert documents[0].blocks[0].page == 1
    assert documents[0].blocks[0].bbox == {
        "x": 10,
        "y": 20,
        "width": 300,
        "height": 40,
    }
    assert documents[0].metadata["ocrEngine"] == "PP-OCRv5 korean"
    assert documents[0].warnings == ("OCR_LOW_CONF page 1: 저신뢰 라인 폐기",)
    assert documents[0].blocks[1].text == "점검 항목\t완료"
    assert documents[0].blocks[1].metadata["table"] == {
        "cells": [[{"text": "점검 항목"}, {"text": "완료"}]]
    }
    assert len(documents[0].blocks) == 2


def test_kordoc_ocr_rejects_unexpected_runtime_version(tmp_path: Path) -> None:
    # Given a configured Kordoc 4.7.3 adapter backed by a different executable version.
    command = tmp_path / "wrong_version.py"
    command.write_text("print('4.7.2')", encoding="utf-8")
    image = tmp_path / "scan.png"
    image.write_bytes(b"fixture")
    adapter = KordocOcrAdapter(
        KordocOcrConfig(
            argv=(sys.executable, str(command)),
            version_argv=(sys.executable, str(command)),
            expected_version="4.7.3",
            timeout_seconds=5,
        )
    )

    # When OCR begins.
    with pytest.raises(ParserError, match=r"expected 4\.7\.3"):
        adapter.recognize((image,))

    # Then unreviewed model/runtime drift is rejected before document parsing.


def test_kordoc_ocr_rejects_malformed_json(tmp_path: Path) -> None:
    command = tmp_path / "invalid_json.py"
    command.write_text("print('{broken')", encoding="utf-8")
    image = tmp_path / "scan.png"
    image.write_bytes(b"fixture")
    adapter = KordocOcrAdapter(
        KordocOcrConfig(argv=(sys.executable, str(command)), timeout_seconds=5)
    )

    with pytest.raises(ParserError, match="invalid JSON"):
        adapter.recognize((image,))


def test_kordoc_ocr_rejects_unsuccessful_document(tmp_path: Path) -> None:
    command = tmp_path / "failed_document.py"
    command.write_text(
        "import json; print(json.dumps({'success': False, 'error': 'decode failed'}))",
        encoding="utf-8",
    )
    image = tmp_path / "scan.png"
    image.write_bytes(b"fixture")
    adapter = KordocOcrAdapter(
        KordocOcrConfig(argv=(sys.executable, str(command)), timeout_seconds=5)
    )

    with pytest.raises(ParserError, match="decode failed"):
        adapter.recognize((image,))


def test_kordoc_ocr_times_out(tmp_path: Path) -> None:
    image = tmp_path / "scan.png"
    image.write_bytes(b"fixture")
    adapter = KordocOcrAdapter(
        KordocOcrConfig(
            argv=(sys.executable, "-c", "import time; time.sleep(1)"),
            timeout_seconds=0.01,
        )
    )

    with pytest.raises(ParserError, match="command failed"):
        adapter.recognize((image,))


def test_kordoc_ocr_preserves_multi_file_order(tmp_path: Path) -> None:
    command = tmp_path / "ordered_documents.py"
    command.write_text(
        """
import json
import sys
for path in sys.argv[1:]:
    print(json.dumps({
        "success": True,
        "blocks": [{"type": "paragraph", "text": path}]
    }))
""".strip(),
        encoding="utf-8",
    )
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    adapter = KordocOcrAdapter(
        KordocOcrConfig(argv=(sys.executable, str(command)), timeout_seconds=5)
    )

    documents = adapter.recognize((first, second))

    assert [document.source_path for document in documents] == [first, second]
    assert [document.blocks[0].text for document in documents] == [
        str(first),
        str(second),
    ]


def test_registry_enables_pinned_korean_ocr_from_settings(tmp_path: Path) -> None:
    # Given an explicit local Kordoc OCR configuration.
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "parsers": {
                "ocr": {
                    "kordoc": {
                        "enabled": True,
                        "argv": ["/opt/kordoc/bin/kordoc", "--format", "json", "--ocr"],
                        "version_argv": ["/opt/kordoc/bin/kordoc", "--version"],
                        "expected_version": "4.7.3",
                    }
                }
            }
        },
    )

    # When the shared parser registry is constructed.
    registry = ParserRegistry.from_settings(settings)

    # Then PDF and PPTX parsers share the pinned Korean OCR adapter.
    pdf = next(parser for parser in registry.parsers if isinstance(parser, PdfParser))
    assert pdf._ocr is not None
    assert pdf._ocr.name == "kordoc-ppocrv5-korean"
    assert pdf._ocr.version == "4.7.3"


def test_registry_rejects_enabled_ocr_without_version_check(tmp_path: Path) -> None:
    # Given enabled OCR whose executable version cannot be proven.
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "parsers": {
                "ocr": {
                    "kordoc": {
                        "enabled": True,
                        "argv": ["/opt/kordoc/bin/kordoc", "--format", "json", "--ocr"],
                        "expected_version": "4.7.3",
                    }
                }
            }
        },
    )

    # When the production parser registry is constructed.
    with pytest.raises(ConfigurationError, match="version_argv"):
        ParserRegistry.from_settings(settings)

    # Then an unpinned runtime cannot enter the parsing path.


def test_registry_rejects_runtime_package_download_command(tmp_path: Path) -> None:
    # Given OCR configured through npx, which may download executable code at parse time.
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "parsers": {
                "ocr": {
                    "kordoc": {
                        "enabled": True,
                        "argv": ["npx", "kordoc@4.7.3", "--format", "json", "--ocr"],
                        "version_argv": ["npx", "kordoc@4.7.3", "--version"],
                        "expected_version": "4.7.3",
                    }
                }
            }
        },
    )

    # When the production parser registry is constructed.
    with pytest.raises(ConfigurationError, match="installed Kordoc binary"):
        ParserRegistry.from_settings(settings)

    # Then indexing never performs an implicit package download.
