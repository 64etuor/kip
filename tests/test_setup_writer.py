from __future__ import annotations

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
    assert '"KIP_CONFIG": "config/kip.generated.toml"' in generated
    assert '"KIP_WORKSPACE": "acme-rnd"' in generated
    assert "KIP_OPENAI_API_KEY" not in generated
    assert ".mcp.json" in receipt.written_files
    assert ".mcp.json.previous" in receipt.previous_files


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
