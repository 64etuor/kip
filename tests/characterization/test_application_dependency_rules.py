from __future__ import annotations

from pathlib import Path

from kip.architecture_rules import (
    application_adapter_imports,
    domain_adapter_imports,
    vendor_sdk_imports,
)

ROOT = Path(__file__).resolve().parents[2]


def test_application_does_not_import_concrete_adapters() -> None:
    # Given the application layer source tree
    application_root = ROOT / "src/kip/application"

    # When its imports are inspected structurally
    violations = application_adapter_imports(ROOT, application_root)

    # Then all dependencies point inward through ports instead of adapters
    assert violations == []


def test_domain_does_not_import_concrete_adapters() -> None:
    # Given the domain layer source tree
    domain_root = ROOT / "src/kip/domain"

    # When its imports are inspected structurally
    violations = domain_adapter_imports(ROOT, domain_root)

    # Then all dependencies point inward through ports instead of adapters
    assert violations == []


def test_application_does_not_import_vendor_sdks() -> None:
    # Given the application layer source tree
    application_root = ROOT / "src/kip/application"

    # When its imports are inspected for direct vendor SDK usage
    violations = vendor_sdk_imports(ROOT, application_root)

    # Then all vendor integrations stay behind an adapter
    assert violations == []


def test_domain_does_not_import_vendor_sdks() -> None:
    # Given the domain layer source tree
    domain_root = ROOT / "src/kip/domain"

    # When its imports are inspected for direct vendor SDK usage
    violations = vendor_sdk_imports(ROOT, domain_root)

    # Then all vendor integrations stay behind an adapter
    assert violations == []
