from __future__ import annotations

import ast
from pathlib import Path


def imports_matching_prefixes(
    root: Path,
    base: Path,
    prefixes: tuple[str, ...],
) -> list[str]:
    violations: list[str] = []
    for path in sorted(base.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
                line_number = node.lineno
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
                line_number = node.lineno
            else:
                continue
            for module in modules:
                if any(
                    module == prefix or module.startswith(f"{prefix}.")
                    for prefix in prefixes
                ):
                    relative = path.relative_to(root)
                    violations.append(f"{relative}:{line_number}: {module}")
    return violations


def application_adapter_imports(root: Path, base: Path) -> list[str]:
    return imports_matching_prefixes(root, base, ("kip.adapters",))


def domain_adapter_imports(root: Path, base: Path) -> list[str]:
    return imports_matching_prefixes(root, base, ("kip.adapters",))


# Vendor SDKs must stay behind adapters. Application and domain code should
# depend only on ports, never import a vendor client library directly.
VENDOR_SDK_PREFIXES: tuple[str, ...] = (
    "psycopg",
    "httpx",
    "anthropic",
    "openai",
    "neo4j",
    "slack_sdk",
    "rapidfuzz",
)


def vendor_sdk_imports(root: Path, base: Path) -> list[str]:
    return imports_matching_prefixes(root, base, VENDOR_SDK_PREFIXES)
