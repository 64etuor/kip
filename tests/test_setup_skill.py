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


def test_setup_skill_covers_bootstrap_prerequisite_and_post_apply_path() -> None:
    text = (ROOT / "skills/kip-setup/SKILL.md").read_text(encoding="utf-8")

    # Fresh-clone prerequisite: bootstrap before the state machine can run.
    assert "./scripts/bootstrap.sh" in text
    assert "3.12" in text
    # Post-apply path: configuration only until app-up and sync complete.
    assert "./scripts/app-up.sh" in text
    assert "configuration-only" in text
    assert "runtime_readiness" in text
    assert "next_steps" in text
    # Secret schemes match the runtime resolvers exactly.
    assert "keychain" in text and "rejected" in text
    assert "env:" in text
    assert "file:/absolute/path" in text
    # sync_schedule is declarative until an operator installs a scheduler.
    assert "sync_schedule" in text
    assert "declarative" in text


def _tree(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
