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
