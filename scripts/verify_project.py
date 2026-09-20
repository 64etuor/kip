#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from kip.architecture_rules import (
    edge_dependency_violations,
    layer_dependency_violations,
)
from kip.documentation import documentation_link_errors, pinned_repository_links
from kip.ontology import validate_ontology
from kip.package_archive_policy import selected_source_files

ROOT = Path(__file__).resolve().parents[1]
# Commit-pinned links into this repository are verified against the checkout
# history. The root commit tells a KIP checkout apart from a recipient's own
# repository that merely contains the extracted package.
REPOSITORY = "64etuor/kip"
ROOT_COMMIT = "95808190211336937f4e680d0f8a6da523eb7d91"


def _skill_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != ".DS_Store"
    }


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _tracked_files(root: Path) -> dict[str, bytes] | None:
    """Every git-tracked file present in the worktree, Markdown bodies loaded.

    Returns None only when ``root`` is not a git checkout (a package recipient
    running this script). Any other git failure raises, so a broken index or
    an ownership refusal cannot turn the repository link check into a silent
    skip that still prints a green verdict.
    """
    if not (root / ".git").exists():
        return None
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, check=True,
    ).stdout
    files: dict[str, bytes] = {}
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        name = raw.decode("utf-8", "surrogateescape")
        path = root / name
        if not path.is_file():
            continue
        files[name] = path.read_bytes() if name.endswith(".md") else b""
    return files


def _repository_link_errors(root: Path, packaged_errors: list[str], *, anchor: str = ROOT_COMMIT) -> list[str]:
    """Link errors of every tracked document, without the ones already reported.

    Shipped documents are checked against the package by the caller. Historical
    records and plans stay in the repository only, so their links are checked
    against the tracked worktree; otherwise they rot unnoticed. Every tracked
    document is checked, shipped or not, and a defect the packaged pass already
    reported is listed once. Commit-pinned links are verified only when the
    checkout holds ``anchor`` (this repository's root commit): a recipient who
    committed the extracted package into their own repository has no KIP
    history and is not blamed for it, while a shallow clone of this
    repository fails once, by name.
    """
    try:
        tracked = _tracked_files(root)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        return [f"git ls-files failed, repository link check did not run: {detail}"]
    except OSError as exc:
        return [f"git ls-files failed, repository link check did not run: {exc}"]
    if tracked is None:
        return []
    if not tracked:
        # An empty listing inside a checkout means git answered with nothing
        # (for example a redirected index), which is a failed check, not a
        # repository without documents.
        return ["git ls-files listed no tracked files, repository link check did not run"]
    already = set(packaged_errors)
    errors = [
        error for error in documentation_link_errors(tracked, scope="repository")
        if error.replace(" repository link target missing: ", " packaged link target missing: ", 1) not in already
    ]
    try:
        errors.extend(_pinned_link_errors(root, tracked, anchor))
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        errors.append(f"git rev-parse failed, pinned link check did not run: {detail}")
    return errors


def _pinned_link_errors(root: Path, tracked: dict[str, bytes], anchor: str) -> list[str]:
    links = pinned_repository_links(tracked, REPOSITORY)
    if not links:
        return []
    if not _git_object_exists(root, f"{anchor}^{{commit}}"):
        if _git_is_shallow(root):
            return [
                "this clone is shallow, so the commit-pinned links cannot be verified; "
                "fetch the full history (git fetch --unshallow) and run again"
            ]
        return []  # not a KIP checkout: the package sits inside another repository
    errors: list[str] = []
    known_revisions: dict[str, bool] = {}
    for name, number, revision, path in links:
        if "\x00" in path or not path or ".." in path.split("/"):
            errors.append(f"{name}:{number}: pinned link path is not valid: {path!r}")
            continue
        if revision not in known_revisions:
            known_revisions[revision] = _git_object_exists(root, f"{revision}^{{commit}}")
        if not known_revisions[revision]:
            errors.append(f"{name}:{number}: pinned revision is not in this repository's history: {revision[:12]}")
            continue
        if not _git_object_exists(root, f"{revision}:{path}"):
            errors.append(f"{name}:{number}: pinned link target missing in history: {revision[:12]}:{path}")
    return errors


def _git_object_exists(root: Path, spec: str) -> bool:
    """True when ``spec`` resolves in this checkout's history.

    A ``<revision>:<path>`` spec is resolved through trees, so the blob itself
    need not be present locally and a blobless partial clone verifies offline;
    ``cat-file -e`` would not. ``rev-parse --verify -q`` exits 1 for an absent
    object or path and 128 for a failure such as a broken repository or a path
    outside it; only the former is "absent", the latter is surfaced by the
    caller.
    """
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "-q", spec], capture_output=True
    )
    if probe.returncode == 0:
        return True
    if probe.returncode == 1:
        return False
    raise subprocess.CalledProcessError(probe.returncode, probe.args, probe.stdout, probe.stderr)


def _git_is_shallow(root: Path) -> bool:
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-shallow-repository"], capture_output=True, text=True
    )
    return probe.returncode == 0 and probe.stdout.strip() == "true"


def main() -> int:
    errors: list[str] = []
    packaged_errors = documentation_link_errors({
        path.relative_to(ROOT).as_posix(): path.read_bytes()
        for path in selected_source_files(ROOT)
    })
    errors.extend(packaged_errors)
    errors.extend(_repository_link_errors(ROOT, packaged_errors))
    # pyproject.toml puts the repository root on sys.path for pytest, so a root
    # `kip/` directory would shadow `src/kip/` in every test run.
    require(not (ROOT / "kip").exists(), "a root kip/ directory shadows src/kip on the pytest path; remove it", errors)
    require((ROOT / "AGENTS.md").is_file(), "AGENTS.md must exist at project root", errors)
    require((ROOT / "CLAUDE.md").is_file(), "CLAUDE.md must exist at project root", errors)
    if (ROOT / "CLAUDE.md").exists():
        require((ROOT / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md", "CLAUDE.md must import @AGENTS.md", errors)
    require((ROOT / ".mcp.json").is_file(), ".mcp.json must exist at project root", errors)
    require((ROOT / "docs/PRD.md").is_file(), "docs/PRD.md is missing", errors)
    require((ROOT / "docs/TRD.md").is_file(), "docs/TRD.md is missing", errors)
    require(
        (ROOT / "docs/PRODUCTION_DESIGN_ALIGNMENT.md").is_file(),
        "docs/PRODUCTION_DESIGN_ALIGNMENT.md is missing",
        errors,
    )
    require((ROOT / "skills/knowledge-fabric/SKILL.md").is_file(), "project Skill is missing", errors)
    require((ROOT / ".claude/skills/knowledge-fabric/SKILL.md").is_file(), "Claude project Skill is missing", errors)
    errors.extend(validate_ontology(ROOT / "ontology"))

    for skill_name in ("knowledge-fabric", "kip-setup"):
        claude_skill_root = ROOT / f".claude/skills/{skill_name}"
        project_skill_root = ROOT / f"skills/{skill_name}"
        require(project_skill_root.is_dir(), f"portable {skill_name} Skill is missing", errors)
        require(claude_skill_root.is_dir(), f"Claude {skill_name} Skill is missing", errors)
        if claude_skill_root.exists() and project_skill_root.exists():
            project_files = _skill_files(project_skill_root)
            claude_files = _skill_files(claude_skill_root)
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

    # The layering rules live in `kip.architecture_rules` so this gate, the
    # boundary test and the characterization test cannot drift apart.
    errors.extend(layer_dependency_violations(ROOT))
    errors.extend(edge_dependency_violations(ROOT))

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
