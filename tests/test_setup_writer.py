from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

from kip.errors import ConflictError, ValidationError
from kip.setup.models import (
    FilesystemSourceAnswer,
    SecretReference,
    SetupAnswers,
)
from kip.setup.planner import build_setup_plan
from kip.setup.service import SetupService
from kip.setup.writer import apply_setup_plan


def test_apply_writes_generated_files_atomically_and_preserves_previous(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(_complete_answers(tmp_path), project_root=project_root)

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
    plan = build_setup_plan(_complete_answers(tmp_path), project_root=project_root)

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
    plan = build_setup_plan(_complete_answers(tmp_path), project_root=project_root)

    receipt = apply_setup_plan(plan, project_root=project_root)

    generated = (project_root / ".mcp.json").read_text(encoding="utf-8")
    assert '"KIP_CONFIG": "config/kip.host.generated.toml"' in generated
    assert '"KIP_WORKSPACE": "acme-rnd"' in generated
    assert "KIP_OPENAI_API_KEY" not in generated
    assert ".mcp.json" in receipt.written_files
    assert ".mcp.json.previous" in receipt.previous_files


def test_generated_compose_mounts_generated_config_for_runtime(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(_complete_answers(tmp_path), project_root=project_root)

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
            }
        ]


def test_generated_host_config_uses_host_paths_for_mcp(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    answers = _complete_answers(tmp_path)
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
        "/sources/company-docs"
    )
    assert container_config["storage"]["cas_path"] == "/var/lib/kip/cas"
    assert host_config["setup"]["plan_fingerprint"] == plan.plan_fingerprint


def test_generated_config_defaults_to_reranked_search_mode(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(_complete_answers(tmp_path), project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    for name in ("config/kip.generated.toml", "config/kip.host.generated.toml"):
        text = (project_root / name).read_text(encoding="utf-8")
        assert 'default_mode = "reranked"' in text
        assert "semantic_enabled = false" in text


def test_generated_configs_enable_pinned_korean_ocr(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(_complete_answers(tmp_path), project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    for name in ("config/kip.generated.toml", "config/kip.host.generated.toml"):
        with (project_root / name).open("rb") as handle:
            config = tomllib.load(handle)
        kordoc = config["parsers"]["ocr"]["kordoc"]
        assert kordoc == {
            "enabled": True,
            "argv": ["kordoc", "--format", "json", "--ocr", "--silent"],
            "version_argv": ["kordoc", "--version"],
            "expected_version": "4.7.3",
        }
        assert config["parsers"]["hwp"]["hwp-hwpx-parser"]["enabled"] is True


def test_generated_config_defaults_auto_approve_to_opt_in_disabled(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(_complete_answers(tmp_path), project_root=project_root)

    apply_setup_plan(plan, project_root=project_root)

    for name in ("config/kip.generated.toml", "config/kip.host.generated.toml"):
        with (project_root / name).open("rb") as handle:
            config = tomllib.load(handle)
        assert config["ontology"]["auto_approve"]["enabled"] is False


def test_generated_config_enables_the_promoted_bm25_reranker(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(_complete_answers(tmp_path), project_root=project_root)

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
    plan = build_setup_plan(_complete_answers(tmp_path), project_root=project_root)
    tampered = plan.model_copy(
        update={"database_secret_ref": SecretReference.parse("env:OTHER_DATABASE_URL")}
    )

    with pytest.raises(ConflictError, match="fingerprint"):
        apply_setup_plan(tampered, project_root=project_root)

    assert not (project_root / "config/kip.generated.toml").exists()


def test_setup_state_save_and_resume_is_atomic(tmp_path: Path) -> None:
    state = tmp_path / "state/setup.json"
    service = SetupService(project_root=tmp_path / "project", state_path=state)

    service.record_answer("workspace", "acme-rnd")
    restored = SetupService(
        project_root=tmp_path / "project",
        state_path=state,
    ).load_answers()

    assert restored.workspace == "acme-rnd"
    assert not list(state.parent.glob("*.tmp"))


def test_setup_state_revalidates_tampered_source_roots(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path).model_copy(
        update={
            "filesystem_sources": [
                _complete_answers(tmp_path).filesystem_sources[0].model_copy(
                    update={"root": "/"}
                )
            ]
        }
    )
    state.write_text(answers.model_dump_json(), encoding="utf-8")

    with pytest.raises(ValidationError, match="too broad"):
        SetupService(
            project_root=tmp_path / "project",
            state_path=state,
        ).load_answers()


def test_managed_storage_cannot_overlap_read_only_source(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path).model_copy(update={"cas_path": None})
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    source = answers.filesystem_sources[0]
    service = SetupService(project_root=project_root, state_path=state)

    with pytest.raises(ValidationError, match="overlap"):
        service.record_answer("cas_path", str(Path(source.root) / "cas"))

    assert not (Path(source.root) / "cas").exists()


def _complete_answers(tmp_path: Path) -> SetupAnswers:
    source = tmp_path / "company-docs"
    source.mkdir(exist_ok=True)
    backup = tmp_path / "backup"
    backup.mkdir(exist_ok=True)
    return SetupAnswers(
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
                project_root=tmp_path / "project",
            )
        ],
        model_provider="openai",
        model_egress_classifications=["public", "internal"],
        model_retention_policy="zero_retention",
        model_secret_ref=SecretReference.parse("env:KIP_OPENAI_API_KEY"),
        database_secret_ref=SecretReference.parse("env:KIP_DATABASE_URL"),
        cas_path=str((tmp_path / "cas").resolve()),
        backup_path=str(backup.resolve()),
        retention_days=365,
        sync_schedule="0 * * * *",
        evaluation_dataset="none",
        interaction_memory_mode="explicit_consent",
        ontology_reviewers=["knowledge-owner@example.invalid"],
    )
