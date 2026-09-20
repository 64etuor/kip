"""The one place KIP's layering is defined.

`scripts/verify_project.py`, `tests/test_dependency_boundaries.py` and
`tests/characterization/test_application_dependency_rules.py` all consume the
functions below, so the gate, the boundary test and the characterization test
can never enforce three different rules.

Two rules are enforced:

1. **Layer rule (allow-list).** `src/kip/domain`, `src/kip/ports` and
   `src/kip/application` may import the standard library and only the `kip.*`
   modules named in their allow-list. An allow-list beats a ban-list because a
   new infrastructure module at the `kip.*` top level (a YAML reader, a
   `tomllib` config loader, a file-locking release writer) is caught the day it
   is imported instead of the day someone remembers to extend a ban-list.
2. **Edge rule.** The process entry points must not import each other and must
   not import `kip.adapters`; they receive adapters from `kip.container`.

The vendor-root ban that the gate and the boundary test used to hold
separately is kept inside the layer rule as a belt-and-braces check: the
allow-list already excludes every third-party package, but naming the vendor
roots explicitly gives a far more actionable message than "not allow-listed".
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

# Vendor SDKs must stay behind adapters. Union of the two lists that
# `scripts/verify_project.py` and `tests/test_dependency_boundaries.py` used to
# keep separately, which is why neither of them agreed with the other.
VENDOR_SDK_PREFIXES: tuple[str, ...] = (
    "anthropic",
    "fastapi",
    "fitz",
    "httpx",
    "mcp",
    "neo4j",
    "openai",
    "openpyxl",
    "psycopg",
    "rapidfuzz",
    "slack_sdk",
)

# `kip.*` modules the application layer may import.
#
# `kip.domain`, `kip.ports` and `kip.application` are the layer itself and the
# two layers it is allowed to depend on. The remaining three are leaf modules
# at the `kip.*` top level that were each inspected and import nothing but the
# standard library (plus `pydantic`, which the domain models already use):
#
# - `kip.errors`  — the shared error taxonomy every layer raises.
# - `kip.ids`     — pure id derivation (`hashlib`, `re`, `uuid`).
# - `kip.database_port` — pure `os.environ` + URL parsing that decides whether
#   a configured database port matches the one `scripts/common.sh` guards.
#   `kip doctor` reports its verdict rather than refusing, so the diagnostics
#   use case reads it directly; there is no vendor client and no adapter
#   behind it, so a port would only wrap a pure function.
# - `kip.skill_installs` — same shape: `json` + `pathlib` reads of the local
#   skill install manifests, reported by one doctor check.
#
# Anything else at the `kip.*` top level is infrastructure (it loads TOML,
# parses YAML, takes file locks) and belongs behind a port.
APPLICATION_ALLOWED_KIP_PREFIXES: tuple[str, ...] = (
    "kip.application",
    "kip.database_port",
    "kip.domain",
    "kip.errors",
    "kip.ids",
    "kip.ports",
    "kip.skill_installs",
)

# The same allow-list minus `kip.application`: the domain and the ports are
# inside the application layer, never above it. Ports may import the domain.
INWARD_ALLOWED_KIP_PREFIXES: tuple[str, ...] = tuple(
    prefix for prefix in APPLICATION_ALLOWED_KIP_PREFIXES if prefix != "kip.application"
)

LAYER_ALLOW_LISTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("src/kip/domain", INWARD_ALLOWED_KIP_PREFIXES),
    ("src/kip/ports", INWARD_ALLOWED_KIP_PREFIXES),
    ("src/kip/application", APPLICATION_ALLOWED_KIP_PREFIXES),
)

# Process entry points. Each one owns a transport and gets its services from
# `kip.container`; none of them is a library for the others.
EDGE_MODULES: tuple[str, ...] = (
    "kip.api",
    "kip.cli",
    "kip.mcp_server",
    "kip.package_archive_cli",
    "kip.setup_cli",
    "kip.worker",
)

# `pyproject.toml` declares five console scripts (`kip`, `kip-api`,
# `kip-worker`, `kip-mcp`, `kip-package`); the sixth edge, `kip.setup_cli`, is
# the setup command group the `kip` script mounts. That human entry point is
# the one people actually type, so it mounts setup and runs the MCP stdio
# server and the background worker in-process for `kip setup`, `kip mcp` and
# `kip worker run` rather than making the operator find `kip-mcp` or `kip-worker`.
# That composition is the entry point's job and is allow-listed by name; every
# other edge-to-edge import is a defect. The rule reads the whole module tree,
# so deferring an import into the command body does not exempt it — a lazy
# `from kip.worker import run_worker` needs this allowance exactly like the
# two module-level ones. Moving the three mounts into a dedicated launcher
# module would empty this set.
EDGE_COMPOSITION_ALLOWANCES: frozenset[tuple[str, str]] = frozenset(
    {
        ("kip.cli", "kip.mcp_server"),
        ("kip.cli", "kip.setup_cli"),
        ("kip.cli", "kip.worker"),
    }
)


def _package_of(path: Path, root: Path) -> str:
    """The dotted package `path` lives in, as an import statement names it.

    The path relative to `root`, minus a leading `src` layout directory and
    minus the file itself: `src/kip/application/search.py` lives in
    `kip.application`.
    """
    parts = list(path.relative_to(root).parts[:-1])
    if parts and parts[0] == "src":
        parts = parts[1:]
    return ".".join(parts)


def _relative_import_target(node: ast.ImportFrom, package: str) -> str:
    """The absolute name a `from . import x` / `from ..y import z` resolves to.

    A relative import reaches exactly the modules an absolute one reaches, so
    it has to be checked as the same name. Without this,
    `from ..adapters.repository import x` inside the application layer passed
    a rule that `import kip.adapters.repository` fails.
    """
    parts = package.split(".") if package else []
    if node.level - 1 > len(parts):
        # Deeper than the package tree: invalid at runtime, and it must not
        # collapse to a short name the allow-list happens to permit.
        return f"<unresolvable relative import level {node.level}>"
    ascended = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
    if node.module:
        ascended = [*ascended, *node.module.split(".")]
    return ".".join(ascended)


def module_imports(path: Path, root: Path) -> Iterator[tuple[int, str]]:
    """Every module name imported by `path`, absolute, with its line number.

    Raises `SyntaxError` when `path` does not parse; callers turn that into a
    violation, because a file the rule cannot read is a gate failure and not
    a pass.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = _package_of(path, root)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = (
                node.module or ""
                if node.level == 0
                else _relative_import_target(node, package)
            )
            if module:
                yield node.lineno, module


def _matches(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes
    )


def layer_import_violations(
    root: Path,
    base: Path,
    allowed_kip_prefixes: tuple[str, ...],
) -> list[str]:
    """Imports below `base` that the layer's allow-list does not permit.

    A `kip.*` import must match `allowed_kip_prefixes`; any other import must
    not be a vendor SDK root. Everything else (the standard library, `pydantic`
    which the domain models are built from) is left alone.
    """
    violations: list[str] = []
    for path in sorted(base.rglob("*.py")):
        relative = path.relative_to(root)
        try:
            imports = list(module_imports(path, root))
        except SyntaxError as exc:
            violations.append(f"syntax error in {relative}: {exc}")
            continue
        for line_number, module in imports:
            if module == "kip" or module.startswith("kip."):
                if not _matches(module, allowed_kip_prefixes):
                    violations.append(
                        f"{relative}:{line_number}: {module} is not on the "
                        f"{base.name} allow-list "
                        f"({', '.join(allowed_kip_prefixes)})"
                    )
            elif _matches(module, VENDOR_SDK_PREFIXES):
                violations.append(
                    f"{relative}:{line_number}: vendor SDK {module} must stay "
                    "behind an adapter"
                )
    return violations


def layer_dependency_violations(root: Path) -> list[str]:
    """Allow-list violations across the domain, the ports and the application."""
    violations: list[str] = []
    for relative_base, allowed in LAYER_ALLOW_LISTS:
        violations.extend(
            layer_import_violations(root, root / relative_base, allowed)
        )
    return violations


def edge_dependency_violations(root: Path) -> list[str]:
    """Edge modules importing a sibling edge or a concrete adapter."""
    violations: list[str] = []
    edges = set(EDGE_MODULES)
    for module_name in EDGE_MODULES:
        path = root / "src" / Path(*module_name.split(".")).with_suffix(".py")
        if not path.is_file():
            violations.append(f"edge module {module_name} is missing")
            continue
        relative = path.relative_to(root)
        try:
            imports = list(module_imports(path, root))
        except SyntaxError as exc:
            violations.append(f"syntax error in {relative}: {exc}")
            continue
        for line_number, imported in imports:
            if _matches(imported, ("kip.adapters",)):
                violations.append(
                    f"{relative}:{line_number}: edge imports concrete adapter "
                    f"{imported}; take it from kip.container"
                )
                continue
            root_module = ".".join(imported.split(".")[:2])
            if root_module in edges and root_module != module_name:
                if (module_name, root_module) in EDGE_COMPOSITION_ALLOWANCES:
                    continue
                violations.append(
                    f"{relative}:{line_number}: edge imports sibling edge {imported}"
                )
    return violations
