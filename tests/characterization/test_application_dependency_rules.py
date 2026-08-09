from __future__ import annotations

from pathlib import Path

from kip.architecture_rules import application_adapter_imports

ROOT = Path(__file__).resolve().parents[2]


def test_application_does_not_import_concrete_adapters() -> None:
    # Given the application layer source tree
    application_root = ROOT / "src/kip/application"

    # When its imports are inspected structurally
    violations = application_adapter_imports(ROOT, application_root)

    # Then all dependencies point inward through ports instead of adapters
    assert violations == []
