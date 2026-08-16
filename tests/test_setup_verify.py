from __future__ import annotations

from pathlib import Path

import pytest

from kip.domain.egress import (
    DataClassification,
    EgressPolicy,
    EgressProvider,
    RetentionPolicy,
)
from kip.errors import ValidationError
from kip.setup.models import (
    FilesystemSourceAnswer,
    SecretReference,
    SetupAnswers,
)
from kip.setup.planner import build_setup_plan
from kip.setup.service import SetupService
from kip.setup.writer import apply_setup_plan


def test_verify_reports_runtime_readiness_without_failing_config_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path)
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    service = SetupService(project_root=project_root, state_path=state)
    plan = build_setup_plan(answers, project_root=project_root)
    apply_setup_plan(plan, project_root=project_root)
    monkeypatch.delenv("KIP_DATABASE_URL", raising=False)
    monkeypatch.delenv("KIP_DATABASE_URL_FILE", raising=False)

    receipt = service.verify(plan)

    # Configuration checks decide `verified`; environment gaps stay actionable.
    assert receipt.verified is True
    readiness = {check.name: check for check in receipt.runtime_readiness}
    assert readiness["python_version"].ok is True
    database = readiness["database_secret"]
    assert database.ok is False
    assert "KIP_DATABASE_URL" in database.detail
    source = readiness["source_readable:company-docs"]
    assert source.ok is True
    assert receipt.next_steps == [
        "./scripts/migrate.sh",
        "./scripts/app-up.sh",
        "./scripts/kip sync run --source company-docs",
        './scripts/kip search "smoke test query" --limit 5',
    ]
    assert any("configuration" in item for item in receipt.limitations)


def test_verify_resolves_database_secret_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path)
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    service = SetupService(project_root=project_root, state_path=state)
    plan = build_setup_plan(answers, project_root=project_root)
    apply_setup_plan(plan, project_root=project_root)
    monkeypatch.setenv("KIP_DATABASE_URL", "postgresql://example.invalid/kip")

    receipt = service.verify(plan)

    readiness = {check.name: check for check in receipt.runtime_readiness}
    assert readiness["database_secret"].ok is True


def test_env_only_secret_questions_reject_file_references(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    answers = SetupAnswers(workspace="acme-rnd", identity_mode="api_key")
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    service = SetupService(project_root=tmp_path / "project", state_path=state)

    with pytest.raises(ValidationError, match="env: reference"):
        service.record_answer(
            "identity_api_key_secret_ref",
            "file:/run/secrets/kip-api-key",
        )


def test_database_secret_question_rejects_non_env_schemes(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path).model_copy(
        update={"database_secret_ref": None}
    )
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    service = SetupService(project_root=tmp_path / "project", state_path=state)

    with pytest.raises(ValidationError, match="env: reference"):
        service.record_answer("database_secret_ref", "file:/run/secrets/db-url")
    with pytest.raises(ValidationError, match="env:NAME or file:"):
        service.record_answer("database_secret_ref", "keychain:kip/database")


def test_model_secret_question_accepts_file_reference(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path).model_copy(
        update={"model_secret_ref": None}
    )
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    service = SetupService(project_root=tmp_path / "project", state_path=state)

    inspection = service.record_answer(
        "model_secret_ref",
        "file:/run/secrets/kip-model-key",
    )

    restored = service.load_answers()
    assert restored.model_secret_ref is not None
    assert restored.model_secret_ref.display() == "file:/run/secrets/kip-model-key"
    assert inspection.complete is True


def test_egress_gate_matches_runtime_resolvable_secret_schemes() -> None:
    def policy(secret_reference: str) -> EgressPolicy:
        return EgressPolicy(
            enabled=True,
            provider=EgressProvider.OPENAI,
            allow_remote=True,
            allowed_classifications=(DataClassification.PUBLIC,),
            retention_policy=RetentionPolicy.ZERO_RETENTION,
            secret_reference=secret_reference,
        )

    from kip.domain.egress import ClassifiedEvidence, evaluate_egress

    evidence = [
        ClassifiedEvidence(
            id="unit_1",
            classification=DataClassification.PUBLIC,
        )
    ]
    assert evaluate_egress(policy("env:KIP_OPENAI_API_KEY"), evidence).allowed
    assert evaluate_egress(policy("file:/run/secrets/model-key"), evidence).allowed
    assert not evaluate_egress(policy("keychain:kip/openai"), evidence).allowed


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
        relation_mining_mode="enabled",
        database_secret_ref=SecretReference.parse("env:KIP_DATABASE_URL"),
        cas_path=str((tmp_path / "cas").resolve()),
        backup_path=str(backup.resolve()),
        retention_days=365,
        sync_schedule="0 * * * *",
        evaluation_dataset="none",
        interaction_memory_mode="explicit_consent",
        ontology_reviewers=["knowledge-owner@example.invalid"],
    )
