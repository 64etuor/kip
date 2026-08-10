import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_core_has_no_vendor_imports():
    forbidden = {"psycopg", "neo4j", "fitz", "openpyxl", "fastapi", "mcp", "httpx"}
    for base in [ROOT / "src/kip/domain", ROOT / "src/kip/application", ROOT / "src/kip/ports"]:
        for path in base.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            assert not {name.split(".")[0] for name in imports}.intersection(forbidden), path


def test_application_has_no_adapter_imports() -> None:
    # Given
    application_root = ROOT / "src/kip/application"

    # When
    imported_adapters: dict[Path, list[str]] = {}
    for path in application_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("kip.adapters")
        ]
        if modules:
            imported_adapters[path] = modules

    # Then
    assert imported_adapters == {}
