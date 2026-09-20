from __future__ import annotations

from pathlib import Path

from kip.architecture_rules import (
    APPLICATION_ALLOWED_KIP_PREFIXES,
    EDGE_COMPOSITION_ALLOWANCES,
    EDGE_MODULES,
    INWARD_ALLOWED_KIP_PREFIXES,
    VENDOR_SDK_PREFIXES,
    layer_import_violations,
)

ROOT = Path(__file__).resolve().parents[2]


def test_application_imports_only_its_allow_list() -> None:
    # Given the application layer source tree
    application_root = ROOT / "src/kip/application"

    # When its imports are inspected structurally
    violations = layer_import_violations(
        ROOT,
        application_root,
        APPLICATION_ALLOWED_KIP_PREFIXES,
    )

    # Then every dependency points inward through the domain or a port
    assert violations == []


def test_domain_imports_only_its_allow_list() -> None:
    # Given the domain layer source tree
    domain_root = ROOT / "src/kip/domain"

    # When its imports are inspected structurally
    violations = layer_import_violations(ROOT, domain_root, INWARD_ALLOWED_KIP_PREFIXES)

    # Then the domain depends on nothing above it
    assert violations == []


def test_ports_import_only_their_allow_list() -> None:
    # Given the ports source tree
    ports_root = ROOT / "src/kip/ports"

    # When its imports are inspected structurally
    violations = layer_import_violations(ROOT, ports_root, INWARD_ALLOWED_KIP_PREFIXES)

    # Then a port names domain types only, never an adapter or a use case
    assert violations == []


def test_an_infrastructure_import_is_reported_by_name(tmp_path: Path) -> None:
    # Given an application module that imports the TOML settings loader
    layer = tmp_path / "application"
    layer.mkdir()
    (layer / "leaky.py").write_text("import kip.settings\n", encoding="utf-8")

    # When the application allow-list is applied to it
    violations = layer_import_violations(
        tmp_path,
        layer,
        APPLICATION_ALLOWED_KIP_PREFIXES,
    )

    # Then the rule names the module instead of silently passing
    assert len(violations) == 1
    assert "application/leaky.py:1: kip.settings" in violations[0]


def test_a_vendor_sdk_import_is_reported_by_name(tmp_path: Path) -> None:
    # Given an application module that imports a vendor client directly
    layer = tmp_path / "application"
    layer.mkdir()
    (layer / "leaky.py").write_text("import psycopg\n", encoding="utf-8")

    # When the application allow-list is applied to it
    violations = layer_import_violations(
        tmp_path,
        layer,
        APPLICATION_ALLOWED_KIP_PREFIXES,
    )

    # Then the belt-and-braces vendor check names it
    assert len(violations) == 1
    assert "vendor SDK psycopg must stay behind an adapter" in violations[0]


# The five sets below are pinned to literals on purpose. Each one is a
# permission, and the failure mode of an allow-list is that it widens by one
# entry at a time until it permits everything it was written to forbid. A
# diff here is the review: widening any of these sets is a reviewed decision
# (an ADR or a changelog entry saying why the new name belongs inside the
# layer), never a side effect of making an import work.


def test_the_application_allow_list_is_exactly_these_prefixes() -> None:
    assert APPLICATION_ALLOWED_KIP_PREFIXES == (
        "kip.application",
        "kip.database_port",
        "kip.domain",
        "kip.errors",
        "kip.ids",
        "kip.ports",
        "kip.skill_installs",
    )


def test_the_inward_allow_list_is_the_application_list_without_itself() -> None:
    assert INWARD_ALLOWED_KIP_PREFIXES == (
        "kip.database_port",
        "kip.domain",
        "kip.errors",
        "kip.ids",
        "kip.ports",
        "kip.skill_installs",
    )


def test_the_edges_are_exactly_these_entry_points() -> None:
    assert EDGE_MODULES == (
        "kip.api",
        "kip.cli",
        "kip.mcp_server",
        "kip.package_archive_cli",
        "kip.setup_cli",
        "kip.worker",
    )


def test_only_the_console_script_composes_sibling_edges() -> None:
    assert tuple(sorted(EDGE_COMPOSITION_ALLOWANCES)) == (
        ("kip.cli", "kip.mcp_server"),
        ("kip.cli", "kip.setup_cli"),
        ("kip.cli", "kip.worker"),
    )


def test_the_vendor_sdk_roots_are_exactly_these() -> None:
    assert VENDOR_SDK_PREFIXES == (
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


def test_a_relative_import_cannot_bypass_the_allow_list(tmp_path: Path) -> None:
    # Given an application module that reaches an adapter by relative path
    layer = tmp_path / "src/kip/application"
    layer.mkdir(parents=True)
    (layer / "leaky.py").write_text(
        "from ..adapters.repository import PostgresRepository\n", encoding="utf-8"
    )

    # When the application allow-list is applied to it
    violations = layer_import_violations(
        tmp_path,
        layer,
        APPLICATION_ALLOWED_KIP_PREFIXES,
    )

    # Then it is reported under the absolute name it resolves to, exactly as
    # `import kip.adapters.repository` would be
    assert len(violations) == 1
    assert "src/kip/application/leaky.py:1: kip.adapters.repository" in violations[0]


def test_a_file_the_rule_cannot_parse_is_a_violation_not_a_crash(
    tmp_path: Path,
) -> None:
    # Given a module inside the layer that does not parse
    layer = tmp_path / "src/kip/application"
    layer.mkdir(parents=True)
    (layer / "broken.py").write_text("def (\n", encoding="utf-8")

    # When the allow-list is applied to the layer
    violations = layer_import_violations(
        tmp_path,
        layer,
        APPLICATION_ALLOWED_KIP_PREFIXES,
    )

    # Then the gate reports it instead of raising, so one unparseable file
    # cannot take down the check for every other file
    assert len(violations) == 1
    assert violations[0].startswith("syntax error in src/kip/application/broken.py: ")
