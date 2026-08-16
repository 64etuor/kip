from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_registry_starts_when_optional_extractors_are_not_installed(
    tmp_path: Path,
) -> None:
    script = """
import builtins

blocked = {"PIL", "fitz", "hwp_hwpx_parser", "openpyxl", "pptx"}
original_import = builtins.__import__

def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".", maxsplit=1)[0] in blocked:
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = blocked_import

from kip.adapters.parsers.registry import ParserRegistry
from kip.settings import Settings

capabilities = ParserRegistry.from_settings(Settings.load()).capabilities()
assert {
    "plain-text",
    "csv-table",
    "xlsx-shallow",
    "python-pptx",
    "pymupdf",
} <= capabilities.keys()
"""
    environment = {
        **os.environ,
        "KIP_CONFIG": str(Path(__file__).resolve().parents[1] / "config/kip.example.toml"),
        "KIP_DATABASE_URL": "memory://",
        "KIP_CAS_PATH": str(tmp_path / "cas"),
        "KIP_ENV": "test",
    }

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
