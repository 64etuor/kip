#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from kip.architecture_rules import application_adapter_imports
from kip.ontology import validate_ontology

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def main() -> int:
    errors: list[str] = []
    require((ROOT / "AGENTS.md").is_file(), "AGENTS.md must exist at project root", errors)
    require((ROOT / "CLAUDE.md").is_file(), "CLAUDE.md must exist at project root", errors)
    if (ROOT / "CLAUDE.md").exists():
        require((ROOT / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md", "CLAUDE.md must import @AGENTS.md", errors)
    require((ROOT / ".mcp.json").is_file(), ".mcp.json must exist at project root", errors)
    require((ROOT / "docs/PRD.md").is_file(), "docs/PRD.md is missing", errors)
    require((ROOT / "docs/TRD.md").is_file(), "docs/TRD.md is missing", errors)
    require((ROOT / "skills/knowledge-fabric/SKILL.md").is_file(), "project Skill is missing", errors)
    require((ROOT / ".claude/skills/knowledge-fabric/SKILL.md").is_file(), "Claude project Skill is missing", errors)
    errors.extend(validate_ontology(ROOT / "ontology"))

    for skill_name in ("knowledge-fabric", "kip-setup"):
        claude_skill_root = ROOT / f".claude/skills/{skill_name}"
        project_skill_root = ROOT / f"skills/{skill_name}"
        require(project_skill_root.is_dir(), f"portable {skill_name} Skill is missing", errors)
        require(claude_skill_root.is_dir(), f"Claude {skill_name} Skill is missing", errors)
        if claude_skill_root.exists() and project_skill_root.exists():
            project_files = {
                path.relative_to(project_skill_root): path.read_bytes()
                for path in project_skill_root.rglob("*")
                if path.is_file()
            }
            claude_files = {
                path.relative_to(claude_skill_root): path.read_bytes()
                for path in claude_skill_root.rglob("*")
                if path.is_file()
            }
            require(
                project_files == claude_files,
                f"Claude and portable {skill_name} Skill trees diverged",
                errors,
            )

    mcp_config = ROOT / ".mcp.json"
    if mcp_config.exists():
        try:
            payload = json.loads(mcp_config.read_text(encoding="utf-8"))
            require(isinstance(payload.get("mcpServers"), dict), ".mcp.json has no mcpServers object", errors)
        except json.JSONDecodeError as exc:
            errors.append(f".mcp.json is invalid JSON: {exc}")

    skill_entrypoint = ROOT / "skills/knowledge-fabric/SKILL.md"
    if skill_entrypoint.exists():
        text = skill_entrypoint.read_text(encoding="utf-8")
        require(text.startswith("---\n"), "Skill frontmatter is missing", errors)
        require("name: knowledge-fabric" in text, "Skill name is invalid", errors)
        require("description:" in text, "Skill description is missing", errors)
        require((ROOT / "skills/knowledge-fabric/agents/openai.yaml").is_file(), "Skill UI metadata is missing", errors)

    # Domain/application/ports may import KIP internals and stdlib only, never vendor adapters or SDKs.
    forbidden_roots = {"psycopg", "neo4j", "fitz", "openpyxl", "fastapi", "mcp", "httpx"}
    for base in [ROOT / "src/kip/domain", ROOT / "src/kip/application", ROOT / "src/kip/ports"]:
        for path in base.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                errors.append(f"syntax error in {path.relative_to(ROOT)}: {exc}")
                continue
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    root = name.split(".")[0]
                    if root in forbidden_roots:
                        errors.append(f"vendor dependency {name} imported by {path.relative_to(ROOT)}")

    errors.extend(
        f"concrete adapter imported by application layer: {violation}"
        for violation in application_adapter_imports(
            ROOT,
            ROOT / "src/kip/application",
        )
    )

    for path in (ROOT / "scripts").glob("*.sh"):
        require(path.stat().st_mode & 0o111 != 0, f"shell script is not executable: {path.relative_to(ROOT)}", errors)

    openapi = ROOT / "contracts/openapi.json"
    if openapi.exists():
        try:
            payload = json.loads(openapi.read_text(encoding="utf-8"))
            require(payload.get("openapi", "").startswith("3."), "OpenAPI contract is invalid", errors)
        except json.JSONDecodeError as exc:
            errors.append(f"OpenAPI JSON is invalid: {exc}")
    else:
        errors.append("contracts/openapi.json is missing")

    if errors:
        print("Project verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("Project structure and dependency boundaries verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
