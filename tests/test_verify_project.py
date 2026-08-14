from __future__ import annotations

from pathlib import Path

from scripts.verify_project import _skill_files


def test_skill_files_ignore_macos_metadata(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("content", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"metadata")

    assert _skill_files(tmp_path) == {Path("SKILL.md"): b"content"}
