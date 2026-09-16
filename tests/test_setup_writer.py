from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yaml

from kip.application.semantic import EMBEDDING_DEFAULTS, SEMANTIC_DEFAULT_MODE
from kip.errors import ConflictError
from kip.setup.models import SecretReference
from kip.setup.planner import build_setup_plan
from kip.setup.writer import apply_setup_plan
from tests.setup_support import complete_setup_answers


def test_apply_writes_generated_files_atomically_and_preserves_previous(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)

    first = apply_setup_plan(plan, project_root=project_root)
    config = project_root / "config/kip.generated.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    second = apply_setup_plan(plan, project_root=project_root)

    assert first.written_files == second.written_files
    assert (project_root / "config/kip.generated.toml.previous").is_file()
    assert ":ro" not in (project_root / "compose.generated.yaml").read_text(
        encoding="utf-8"
    )
    assert "read_only: true" in (project_root / "compose.generated.yaml").read_text(
        encoding="utf-8"
    )
    generated = config.read_text(encoding="utf-8")
    assert 'retention_policy = "zero_retention"' in generated


def test_generated_compose_selects_approved_cas_without_yaml_aliases(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    text = (project_root / "compose.generated.yaml").read_text(encoding="utf-8")
    compose = yaml.safe_load(text)
    assert "&id" not in text
    assert "*id" not in text
    assert compose["services"]["api"]["environment"]["KIP_CAS_PATH"] == (
        "/var/lib/kip/cas"
    )
    assert compose["services"]["worker"]["environment"]["KIP_CAS_PATH"] == (
        "/var/lib/kip/cas"
    )


def test_generated_mcp_uses_generated_config_without_secret_material(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".mcp.json").write_text('{"legacy": true}\n', encoding="utf-8")
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)

    receipt = apply_setup_plan(plan, project_root=project_root)

    generated = (project_root / ".mcp.json").read_text(encoding="utf-8")
    # Absolute paths: a client starts the server from its own working
    # directory, and the same entry must work at user scope or in another project.
    root = project_root.resolve()
    assert json.loads(generated) == {
        "mcpServers": {
            "kip": {
                "command": "bash",
                "args": [str(root / "scripts/mcp.sh")],
                "env": {
                    "KIP_CONFIG": str(root / "config/kip.host.generated.toml"),
                    "KIP_WORKSPACE": "acme-rnd",
                },
            }
        }
    }
    assert "KIP_OPENAI_API_KEY" not in generated
    assert ".mcp.json" in receipt.written_files
    assert ".mcp.json.previous" in receipt.previous_files


def test_generated_compose_mounts_generated_config_for_runtime(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    compose = yaml.safe_load(
        (project_root / "compose.generated.yaml").read_text(encoding="utf-8")
    )
    for service_name in ("api", "worker"):
        service = compose["services"][service_name]
        assert service["environment"]["KIP_CONFIG"] == (
            "/app/config/kip.generated.toml"
        )
        config_mounts = [
            volume
            for volume in service["volumes"]
            if volume.get("target") == "/app/config/kip.generated.toml"
        ]
        assert config_mounts == [
            {
                "type": "bind",
                "source": "./config/kip.generated.toml",
                "target": "/app/config/kip.generated.toml",
                "read_only": True,
                "bind": {"create_host_path": False},
            }
        ]


def test_generated_host_config_uses_host_paths_for_mcp(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    answers = complete_setup_answers(tmp_path)
    plan = build_setup_plan(answers, project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    with (project_root / "config/kip.host.generated.toml").open("rb") as handle:
        host_config = tomllib.load(handle)
    with (project_root / "config/kip.generated.toml").open("rb") as handle:
        container_config = tomllib.load(handle)
    assert host_config["storage"]["cas_path"] == answers.cas_path
    assert host_config["operations"]["backup_path"] == answers.backup_path
    assert host_config["sources"]["filesystem"][0]["root"] == (
        answers.filesystem_sources[0].root
    )
    assert host_config["api"]["host"] == "127.0.0.1"
    assert container_config["sources"]["filesystem"][0]["root"] == (
        answers.filesystem_sources[0].root
    )
    assert container_config["storage"]["cas_path"] == "/var/lib/kip/cas"
    assert host_config["setup"]["plan_fingerprint"] == plan.plan_fingerprint


def test_generated_config_is_lexical_only_without_a_model_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a machine where bootstrap did not install the model runtime.
    monkeypatch.delenv("KIP_SEMANTIC", raising=False)
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    # Then generated configs keep lexical search (with BM25 rerank) and say why.
    assert plan.semantic_search is False
    assert any("model runtime is not installed" in warning for warning in plan.warnings)
    for name in ("config/kip.generated.toml", "config/kip.host.generated.toml"):
        with (project_root / name).open("rb") as handle:
            config = tomllib.load(handle)
        assert config["search"]["semantic_enabled"] is False
        assert config["search"]["lexical_rerank_enabled"] is True
        assert config["models"]["embedding"]["enabled"] is False
    compose = yaml.safe_load((project_root / "compose.generated.yaml").read_text(encoding="utf-8"))
    assert "models" not in compose["services"]


def test_generated_config_turns_on_semantic_search_where_the_runtime_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given bootstrap installed the isolated model runtime.
    monkeypatch.delenv("KIP_SEMANTIC", raising=False)
    project_root = tmp_path / "project"
    runtime = project_root / "var/semantic-venv/bin/infinity_emb"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("#!/bin/sh\n", encoding="utf-8")
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    # Then host and container configs use vector search with the shipped defaults.
    assert plan.semantic_search is True
    with (project_root / "config/kip.host.generated.toml").open("rb") as handle:
        host = tomllib.load(handle)
    with (project_root / "config/kip.generated.toml").open("rb") as handle:
        container = tomllib.load(handle)
    for config in (host, container):
        assert config["search"]["semantic_enabled"] is True
        assert config["search"]["default_mode"] == SEMANTIC_DEFAULT_MODE
        assert config["models"]["embedding"]["enabled"] is True
        assert config["models"]["embedding"]["revision"] == EMBEDDING_DEFAULTS["revision"]
    assert host["models"]["embedding"]["base_url"] == "http://127.0.0.1:7997"
    assert host["security"]["model_service_hosts"] == []
    assert container["models"]["embedding"]["base_url"] == "http://models:7997"
    assert container["security"]["model_service_hosts"] == ["models"]
    compose = yaml.safe_load((project_root / "compose.generated.yaml").read_text(encoding="utf-8"))
    assert "@sha256:" in compose["services"]["models"]["image"]

    # And KIP_SEMANTIC=off still opts the same machine out.
    monkeypatch.setenv("KIP_SEMANTIC", "off")
    assert build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root).semantic_search is False


def test_generated_configs_enable_pinned_korean_ocr(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    for name in ("config/kip.generated.toml", "config/kip.host.generated.toml"):
        with (project_root / name).open("rb") as handle:
            config = tomllib.load(handle)
        kordoc = config["parsers"]["ocr"]["kordoc"]
        assert kordoc == {
            "enabled": True,
            "argv": ["kordoc", "--format", "json", "--ocr", "--silent"],
            "version_argv": ["kordoc", "--version"],
            "expected_version": "4.13.1",
        }
        assert config["parsers"]["pdf"] == {
            "backend": "pdf_inspector",
            "tables_enabled": True,
        }
        assert config["parsers"]["hwp"]["hwp-hwpx-parser"]["enabled"] is True
        assert config["parsers"]["isolation"] == {
            "enabled": True,
            "wall_seconds": 180,
            "cpu_seconds": 120,
            "memory_mib": 6144,
            "result_mib": 256,
            "diagnostic_kib": 16,
            "cpu_threads": 4,
            "nice": 5,
        }


def test_generated_config_defaults_auto_approve_to_opt_in_disabled(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    for name in ("config/kip.generated.toml", "config/kip.host.generated.toml"):
        with (project_root / name).open("rb") as handle:
            config = tomllib.load(handle)
        assert config["ontology"]["auto_approve"]["enabled"] is False


def test_generated_config_enables_relation_mining_when_setup_selects_it(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    answers = complete_setup_answers(tmp_path).model_copy(
        update={"relation_mining_mode": "enabled"}
    )
    plan = build_setup_plan(answers, project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    for name in ("config/kip.generated.toml", "config/kip.host.generated.toml"):
        with (project_root / name).open("rb") as handle:
            config = tomllib.load(handle)
        assert config["models"]["relation_mining"] == {
            "enabled": True,
            "max_units": 200,
            "max_characters": 480000,
            "max_entity_proposals": 128,
            "max_relation_proposals": 256,
        }


def test_generated_config_enables_the_promoted_bm25_reranker(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    for name in ("config/kip.generated.toml", "config/kip.host.generated.toml"):
        with (project_root / name).open("rb") as handle:
            config = tomllib.load(handle)
        reranker = config["models"]["reranker"]
        assert reranker["enabled"] is True
        assert reranker["backend"] == "bm25"


def test_apply_rejects_tampered_plan_before_writing(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)
    tampered = plan.model_copy(
        update={"database_secret_ref": SecretReference.parse("env:OTHER_DATABASE_URL")}
    )

    with pytest.raises(ConflictError, match="fingerprint"):
        apply_setup_plan(tampered, project_root=project_root)

    assert not (project_root / "config/kip.generated.toml").exists()


def test_apply_receipt_says_which_files_were_replaced_and_where_the_old_copies_are(
    tmp_path: Path,
) -> None:
    # Given a package project whose shipped .mcp.json the plan lists for replacement
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".mcp.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)
    assert plan.replaced_files == [".mcp.json"]

    # When the approved plan is applied
    receipt = apply_setup_plan(plan, project_root=project_root)

    # Then the receipt names the replacement and the kept copy in plain words
    assert [item.model_dump() for item in receipt.replaced_files] == [
        {"file": ".mcp.json", "previous_copy": ".mcp.json.previous", "original_copy": ".mcp.json.original"}
    ]
    assert ".mcp.json (previous copy: .mcp.json.previous; original copy: .mcp.json.original)" in receipt.summary
    assert "Replaced 1 existing file(s)" in receipt.summary
    assert "Not in the plan" not in receipt.summary
    assert (project_root / ".mcp.json.previous").read_text(encoding="utf-8") == '{"mcpServers": {}}\n'


def test_apply_receipt_flags_replacements_the_plan_did_not_list(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)

    first = apply_setup_plan(plan, project_root=project_root)
    second = apply_setup_plan(plan, project_root=project_root)

    assert first.replaced_files == []
    assert first.summary.endswith("No existing file was replaced.")
    assert len(second.replaced_files) == 4
    assert "did not exist when the plan was made: config/kip.generated.toml" in second.summary


def test_apply_keeps_the_earliest_original_across_repeated_applies(tmp_path: Path) -> None:
    # Given the package's own .mcp.json
    project_root = tmp_path / "project"
    project_root.mkdir()
    package_default = '{"mcpServers": {"package": {}}}\n'
    (project_root / ".mcp.json").write_text(package_default, encoding="utf-8")
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)

    # When the same plan is applied twice
    first = apply_setup_plan(plan, project_root=project_root)
    generated = (project_root / ".mcp.json").read_text(encoding="utf-8")
    second = apply_setup_plan(plan, project_root=project_root)

    # Then .previous rolls forward while .original keeps the package default
    assert (project_root / ".mcp.json.previous").read_text(encoding="utf-8") == generated
    assert (project_root / ".mcp.json.original").read_text(encoding="utf-8") == package_default
    [mcp] = [item for item in second.replaced_files if item.file == ".mcp.json"]
    assert mcp.model_dump() == {
        "file": ".mcp.json", "previous_copy": ".mcp.json.previous", "original_copy": ".mcp.json.original",
    }
    assert first.replaced_files[0].original_copy == ".mcp.json.original"
    assert (
        ".mcp.json (previous copy: .mcp.json.previous; original copy: .mcp.json.original)" in second.summary
    )
    assert "FILE.original the earliest copy, written once and never overwritten" in second.summary


def test_apply_seeds_the_original_from_a_previous_copy_an_older_apply_left(tmp_path: Path) -> None:
    # Given a deployment set up before .original existed: .previous holds the
    # package default and .mcp.json is already generated.
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".mcp.json.previous").write_text('{"package": true}\n', encoding="utf-8")
    (project_root / ".mcp.json").write_text('{"generated": 1}\n', encoding="utf-8")
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    assert (project_root / ".mcp.json.original").read_text(encoding="utf-8") == '{"package": true}\n'
    assert (project_root / ".mcp.json.previous").read_text(encoding="utf-8") == '{"generated": 1}\n'


def test_apply_refuses_a_saved_plan_with_a_comma_acl_scope(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)
    source = plan.sources[0].model_copy(update={"acl_scope": "group:a,b"})
    legacy = plan.model_copy(update={"sources": [source]})
    legacy = legacy.model_copy(update={"plan_fingerprint": legacy.calculate_fingerprint()})

    with pytest.raises(ConflictError) as raised:
        apply_setup_plan(legacy, project_root=project_root)

    assert str(raised.value) == (
        "filesystem source acl_scope contains a comma (company-docs: 'group:a,b'), and an ACL scope cannot: "
        "scopes are comma-separated in KIP_ACL_SCOPES, the X-KIP-ACL-Scopes header and the database session, "
        "so it would become separate scopes. Re-answer filesystem_sources with comma-free scopes, then make "
        "and approve a new plan"
    )
    assert not (project_root / "config/kip.generated.toml").exists()
