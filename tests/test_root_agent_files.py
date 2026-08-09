from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_root_agent_files_present_and_imported():
    assert (ROOT / "AGENTS.md").is_file()
    assert (ROOT / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md"
    assert (ROOT / ".mcp.json").is_file()


def test_project_skill_mirrors_portable_skill():
    portable = ROOT / "skills/knowledge-fabric/SKILL.md"
    claude = ROOT / ".claude/skills/knowledge-fabric/SKILL.md"
    assert portable.read_bytes() == claude.read_bytes()


def test_bootstrap_installs_agent_identity_and_observability_runtime() -> None:
    bootstrap = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")

    assert ".[postgres,api,identity,extractors,mcp,telemetry,dev]" in bootstrap
