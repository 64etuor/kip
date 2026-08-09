from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_setup_skill_trees_are_identical() -> None:
    portable = _tree(ROOT / "skills/kip-setup")
    claude = _tree(ROOT / ".claude/skills/kip-setup")

    assert portable
    assert portable == claude


def test_setup_skill_uses_cli_state_machine_and_one_question_loop() -> None:
    text = (ROOT / "skills/kip-setup/SKILL.md").read_text(encoding="utf-8")

    assert "kip setup inspect" in text
    assert "exactly one" in text
    assert "kip setup answer" in text
    assert "kip setup preview" in text
    assert "kip setup plan" in text
    assert "kip setup apply" in text
    assert "kip setup verify" in text
    assert "Never edit TOML" in text


def _tree(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
