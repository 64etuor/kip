from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Final

from kip.errors import ValidationError

ROOT_FILES: Final = (
    ".dockerignore",
    ".env.example",
    ".gitattributes",
    ".gitignore",
    ".mcp.json",
    "AGENTS.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "Dockerfile",
    "LICENSE",
    "Makefile",
    "README.md",
    "THIRD-PARTY-NOTICES.md",
    "VERSION",
    "compose.production.yaml",
    "compose.yaml",
    "pyproject.toml",
    "uv.lock",
)
FULL_TREES: Final = (
    ".claude/skills",
    ".github",
    "contracts",
    "deploy",
    "docs/adr",
    "evaluation/schemas",
    "examples",
    "migrations",
    "ontology",
    "requirements",
    "sample-data",
    "scripts",
    "sdk",
    "skills",
    "src",
    "tests",
)
CONFIG_FILES: Final = (
    "config/kip.container.toml",
    "config/kip.example.toml",
    "config/logging.yaml",
)
DOCUMENT_FILES: Final = tuple(
    f"docs/{name}"
    for name in (
        "AI_OPERATOR_RUNBOOK.md",
        "APP_INTEGRATION.md",
        "CONNECTORS.md",
        "DATA_CONTRACTS.md",
        "GLOSSARY.md",
        "IMPLEMENTATION_STATUS.md",
        "ONTOLOGY_GUIDE.md",
        "OPERATIONS.md",
        "PRD.md",
        "PRODUCTION_CHECKLIST.md",
        "PRODUCTION_DESIGN_ALIGNMENT.md",
        "QUICKSTART.md",
        "RAG_EVALUATION.md",
        "SECURITY.md",
        "STARTER_KIT_GUIDE.md",
        "TRD.md",
        "TROUBLESHOOTING.md",
    )
)
EVALUATION_FILES: Final = (
    "evaluation/README.md",
    "evaluation/corpus/README.md",
    "evaluation/corpus/public-government.json",
    "evaluation/experiments/example.yaml",
    "evaluation/experiments/quality-audit-20260806-reranker.yaml",
    "evaluation/golden/drafts/README.md",
    "evaluation/golden/drafts/example-draft.yaml",
    "evaluation/golden/example.yaml",
    "evaluation/golden/ontology-starter.yaml",
    "evaluation/golden/private-starter.yaml",
    "evaluation/golden/production-regression.yaml",
    "evaluation/golden/public-government.yaml",
    "evaluation/reports/quality-audit-20260806-all/latest.json",
    "evaluation/reviews/ontology-starter.yaml",
)
REQUIRED_FILES: Final = frozenset(
    {
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "VERSION",
        "compose.production.yaml",
        "config/kip.example.toml",
        "contracts/envelope.schema.json",
        "contracts/starter-archive-manifest.schema.json",
        "docs/PRD.md",
        "docs/SECURITY.md",
        "docs/STARTER_KIT_GUIDE.md",
        "docs/TRD.md",
        "evaluation/golden/production-regression.yaml",
        "migrations/0001_extensions_and_schemas.sql",
        "ontology/core/predicates.yaml",
        "scripts/bootstrap.sh",
        "scripts/verify.sh",
        "skills/kip-setup/SKILL.md",
        "src/kip/__init__.py",
        "tests/test_root_agent_files.py",
    }
)
IGNORED_PARTS: Final = frozenset(
    {".DS_Store", ".pytest_cache", ".ruff_cache", "__pycache__", "worktrees"}
)
FORBIDDEN_PARTS: Final = frozenset(
    {
        ".env",
        ".git",
        ".kip",
        ".omc",
        ".omo",
        ".playwright-cli",
        ".venv",
        "cas",
        "dist",
        "output",
        "secrets",
        "var",
    }
)
FORBIDDEN_SUFFIXES: Final = frozenset(
    {".backup", ".db", ".dump", ".pyc", ".pyo", ".sqlite"}
)
PRIVATE_PATTERNS: Final = (
    re.compile(r"/" + r"Users/[^/\s]+/"),
    re.compile(r"-{5}BEGIN [A-Z ]*PRIVATE KEY-{5}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?:ghp|github_pat|sk-ant|sk-proj)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{16,}"),
)
DATABASE_URL_PATTERN: Final = re.compile(
    r"postgres(?:ql)?://[^:\s]+:([^@\s]+)@", re.IGNORECASE
)
SAFE_EXAMPLE_PASSWORDS: Final = frozenset(
    {
        "change-me",
        "change-me-before-use",
        "ci-test-secret-key-do-not-use-in-production",
        "fixture",
        "kip",
        "redacted",
        "test-password",
        "testpassword",
    }
)
ALLOWED_EXACT: Final = frozenset(
    (*ROOT_FILES, *CONFIG_FILES, *DOCUMENT_FILES, *EVALUATION_FILES)
)


def selected_source_files(root: Path) -> tuple[Path, ...]:
    exact = (*ROOT_FILES, *CONFIG_FILES, *DOCUMENT_FILES, *EVALUATION_FILES)
    selected = {_required_file(root, relative) for relative in exact}
    for relative in FULL_TREES:
        tree = root / relative
        if not tree.is_dir():
            raise ValidationError(f"required starter directory is missing: {relative}")
        selected.update(path for path in tree.rglob("*") if _is_selected_file(path, root))
    return tuple(sorted(selected, key=lambda path: path.relative_to(root).as_posix()))


def validate_relative_path(relative: PurePosixPath) -> None:
    lowered = tuple(part.lower() for part in relative.parts)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValidationError(f"unsafe starter path: {relative}")
    if any(part in FORBIDDEN_PARTS for part in lowered):
        raise ValidationError(f"forbidden starter path: {relative}")
    if any(part.endswith(".egg-info") for part in lowered):
        raise ValidationError(f"generated package metadata in starter path: {relative}")
    if relative.suffix.lower() in FORBIDDEN_SUFFIXES:
        raise ValidationError(f"forbidden starter suffix: {relative}")


def validate_included_path(relative: PurePosixPath) -> None:
    validate_relative_path(relative)
    name = relative.as_posix()
    if name in ALLOWED_EXACT:
        return
    if any(name.startswith(f"{tree}/") for tree in FULL_TREES):
        return
    raise ValidationError(f"nonessential starter path is not allowed: {relative}")


def scan_content(content: bytes, relative: str) -> None:
    if len(content) > 4 * 1024 * 1024:
        return
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return
    for pattern in PRIVATE_PATTERNS:
        if pattern.search(text):
            raise ValidationError(f"private or secret content in starter path: {relative}")
    for match in DATABASE_URL_PATTERN.finditer(text):
        password = match.group(1)
        if "${" not in match.group(0) and password.lower() not in SAFE_EXAMPLE_PASSWORDS:
            raise ValidationError(f"database credential in starter path: {relative}")


def _required_file(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise ValidationError(f"required starter file is missing or unsafe: {relative}")
    validate_relative_path(PurePosixPath(relative))
    return path


def _is_selected_file(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(
        part in IGNORED_PARTS or part.endswith(".egg-info")
        for part in relative.parts
    ):
        return False
    if path.is_symlink():
        raise ValidationError(f"starter source contains a symlink: {relative}")
    if not path.is_file():
        return False
    validate_relative_path(PurePosixPath(relative.as_posix()))
    return True
