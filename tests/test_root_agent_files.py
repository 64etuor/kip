from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_root_agent_files_present_and_imported():
    assert (ROOT / "AGENTS.md").is_file()
    assert (ROOT / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md"
    assert (ROOT / ".mcp.json").is_file()
    assert (ROOT / "docs/PRODUCTION_DESIGN_ALIGNMENT.md").is_file()


_SKIP_NAMES = {".DS_Store"}


def _is_dotfile_noise(path: Path) -> bool:
    return path.name in _SKIP_NAMES or path.name.startswith(".")


def test_all_skill_files_mirrored_byte_for_byte_into_claude_skills():
    portable_root = ROOT / "skills"
    claude_root = ROOT / ".claude/skills"

    portable_files = {
        path.relative_to(portable_root)
        for path in portable_root.rglob("*")
        if path.is_file() and not _is_dotfile_noise(path)
    }
    claude_files = {
        path.relative_to(claude_root)
        for path in claude_root.rglob("*")
        if path.is_file() and not _is_dotfile_noise(path)
    }

    assert claude_files == portable_files, (
        f"missing mirror: {portable_files - claude_files}; "
        f"extra content on .claude side: {claude_files - portable_files}"
    )
    for relative in sorted(portable_files):
        portable_bytes = (portable_root / relative).read_bytes()
        claude_bytes = (claude_root / relative).read_bytes()
        assert portable_bytes == claude_bytes, f"mirror drift: {relative}"


def test_bootstrap_installs_agent_identity_and_observability_runtime() -> None:
    bootstrap = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")

    assert ".[postgres,api,identity,extractors,mcp,telemetry,dev]" in bootstrap
