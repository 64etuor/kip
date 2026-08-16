"""Drift protectors that pin capacity defaults, versioning, and image/doc
parity facts that must never silently diverge across the CLI, MCP server,
setup writer, domain models, compose files, and docs.

These tests are deliberately narrow: they assert facts that would otherwise
be easy to change in one place and forget in another, not every possible
consistency in the codebase.
"""

from __future__ import annotations

import inspect
import re
import tomllib
from pathlib import Path
from typing import Never

import typer.main

from kip.cli import app as cli_app
from kip.domain.json_types import JsonObject
from kip.domain.models import AnswerRequest, ContextRequest
from kip.setup.config_payload import build_config_payload
from kip.setup.models import FilesystemSourceAnswer, SecretReference, SetupAnswers
from kip.setup.planner import build_setup_plan

ROOT = Path(__file__).resolve().parents[1]


def _click_command_default(command_name: str, option_name: str) -> int:
    group = typer.main.get_command(cli_app)
    assert hasattr(group, "commands"), "typer app did not resolve to a command group"
    command = group.commands[command_name]
    for param in command.params:
        if param.name == option_name:
            assert isinstance(param.default, int)
            return param.default
    raise AssertionError(f"no option {option_name!r} on command {command_name!r}")


def _mcp_tool_default(tool_name: str, parameter_name: str) -> int:
    from mcp.server.fastmcp import FastMCP

    from kip import mcp_server

    original_build_container = mcp_server.build_container
    mcp_server.build_container = lambda: _StubContainer()
    try:
        server = mcp_server.create_server()
    finally:
        mcp_server.build_container = original_build_container
    assert isinstance(server, FastMCP)
    tool = server._tool_manager._tools[tool_name]
    signature = inspect.signature(tool.fn)
    default = signature.parameters[parameter_name].default
    assert isinstance(default, int)
    return default


class _StubApplication:
    def __getattr__(self, name: str) -> Never:  # pragma: no cover - never invoked
        raise AssertionError("stub application is only used for signature inspection")


class _StubSettings:
    workspace = "default"


class _StubContainer:
    application = _StubApplication()
    settings = _StubSettings()


def _setup_config_payload(tmp_root: Path) -> JsonObject:
    source = tmp_root / "company-docs"
    source.mkdir(exist_ok=True)
    backup = tmp_root / "backup"
    backup.mkdir(exist_ok=True)
    answers = SetupAnswers(
        workspace="acme-rnd",
        identity_mode="proxy_jwt",
        jwt_issuer="https://identity.example.test/",
        jwt_audience="kip-api",
        jwt_jwks_url="https://identity.example.test/.well-known/jwks.json",
        jwt_admin_groups=["kip-admins"],
        identity_owner="platform-security",
        source_ownership="company",
        ontology_profile="empty",
        filesystem_sources=[
            FilesystemSourceAnswer.from_user_value(
                {
                    "name": "company-docs",
                    "root": str(source),
                    "classification": "internal",
                    "acl_scope": "workspace:acme-rnd",
                },
                project_root=tmp_root / "project",
            )
        ],
        model_provider="openai",
        model_egress_classifications=["public", "internal"],
        model_retention_policy="zero_retention",
        model_secret_ref=SecretReference.parse("env:KIP_OPENAI_API_KEY"),
        database_secret_ref=SecretReference.parse("env:KIP_DATABASE_URL"),
        cas_path=str((tmp_root / "cas").resolve()),
        backup_path=str(backup.resolve()),
        retention_days=365,
        sync_schedule="0 * * * *",
        evaluation_dataset="none",
        interaction_memory_mode="explicit_consent",
        ontology_reviewers=["knowledge-owner@example.invalid"],
    )
    plan = build_setup_plan(answers, project_root=tmp_root / "project")
    return build_config_payload(plan, container=True)


def test_context_max_chars_default_agrees_across_model_cli_mcp_and_writer(
    tmp_path: Path,
) -> None:
    model_default = ContextRequest.model_fields["max_chars"].default
    cli_default = _click_command_default("context", "max_chars")
    mcp_default = _mcp_tool_default("kip_context", "max_chars")
    writer_default = _setup_config_payload(tmp_path)["search"]["context_max_chars"]

    assert model_default == cli_default == mcp_default == writer_default


def test_answer_max_chars_default_agrees_across_model_cli_and_mcp() -> None:
    model_default = AnswerRequest.model_fields["max_chars"].default
    cli_default = _click_command_default("answer", "max_chars")
    mcp_default = _mcp_tool_default("kip_answer", "max_chars")

    assert model_default == cli_default == mcp_default


def test_version_file_matches_pyproject_project_version() -> None:
    version_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert version_file == pyproject["project"]["version"]


def test_postgres_image_digest_matches_between_production_compose_and_ci() -> None:
    pattern = re.compile(r"pgvector/pgvector:[\w.\-]+@sha256:[0-9a-f]+")

    compose_text = (ROOT / "compose.production.yaml").read_text(encoding="utf-8")
    ci_text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    compose_match = pattern.search(compose_text)
    ci_match = pattern.search(ci_text)
    assert compose_match is not None, "no pgvector image found in compose.production.yaml"
    assert ci_match is not None, "no pgvector image found in .github/workflows/ci.yml"
    assert compose_match.group(0) == ci_match.group(0)


def test_every_mcp_tool_function_is_mentioned_in_app_integration_docs() -> None:
    mcp_server_source = (ROOT / "src/kip/mcp_server.py").read_text(encoding="utf-8")
    tool_names = re.findall(r"^\s+def (kip_[a-zA-Z0-9_]+)\(", mcp_server_source, re.MULTILINE)
    assert tool_names, "no kip_* tool functions found in mcp_server.py"

    docs_text = (ROOT / "docs/APP_INTEGRATION.md").read_text(encoding="utf-8")
    missing = [name for name in tool_names if name not in docs_text]

    assert not missing, f"MCP tools missing from docs/APP_INTEGRATION.md: {missing}"
