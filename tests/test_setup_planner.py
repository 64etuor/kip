from __future__ import annotations

from pathlib import Path

from kip.setup.models import (
    FilesystemSourceAnswer,
    SecretReference,
    SetupAnswers,
)
from kip.setup.planner import build_setup_plan, inspect_setup


def test_setup_inspection_asks_only_first_missing_decision(tmp_path: Path) -> None:
    inspection = inspect_setup(SetupAnswers(), project_root=tmp_path)

    assert inspection.complete is False
    assert [question.id for question in inspection.questions] == ["workspace"]


def test_setup_inspection_resumes_with_dynamic_provider_questions(
    tmp_path: Path,
) -> None:
    answers = _complete_answers(tmp_path).model_copy(
        update={"model_egress_classifications": None, "model_secret_ref": None}
    )

    inspection = inspect_setup(answers, project_root=tmp_path)

    assert [question.id for question in inspection.questions] == [
        "model_egress_classifications"
    ]

    retention = inspect_setup(
        answers.model_copy(
            update={
                "model_egress_classifications": ["public"],
                "model_retention_policy": None,
            }
        ),
        project_root=tmp_path,
    )
    assert [question.id for question in retention.questions] == [
        "model_retention_policy"
    ]


def test_proxy_jwt_setup_collects_verification_metadata_one_question_at_a_time(
    tmp_path: Path,
) -> None:
    answers = SetupAnswers(workspace="acme-rnd", identity_mode="proxy_jwt")

    issuer = inspect_setup(answers, project_root=tmp_path)
    audience = inspect_setup(
        answers.model_copy(update={"jwt_issuer": "https://identity.example.test/"}),
        project_root=tmp_path,
    )
    jwks = inspect_setup(
        answers.model_copy(
            update={
                "jwt_issuer": "https://identity.example.test/",
                "jwt_audience": "kip-api",
            }
        ),
        project_root=tmp_path,
    )

    assert [question.id for question in issuer.questions] == ["jwt_issuer"]
    assert [question.id for question in audience.questions] == ["jwt_audience"]
    assert [question.id for question in jwks.questions] == ["jwt_jwks_url"]


def test_setup_plan_is_deterministic_and_contains_read_only_mounts(
    tmp_path: Path,
) -> None:
    answers = _complete_answers(tmp_path)

    first = build_setup_plan(answers, project_root=tmp_path)
    second = build_setup_plan(answers, project_root=tmp_path)

    assert first.plan_fingerprint == second.plan_fingerprint
    assert first.answers_fingerprint == answers.fingerprint()
    assert first.mounts[0].read_only is True
    assert first.mounts[0].target == "/sources/company-docs"
    assert first.generated_files == [
        "config/kip.generated.toml",
        "compose.generated.yaml",
        ".mcp.json",
    ]


def test_setup_plan_does_not_serialize_secret_material(tmp_path: Path) -> None:
    answers = _complete_answers(tmp_path)

    serialized = build_setup_plan(answers, project_root=tmp_path).model_dump_json()

    assert "sk-" not in serialized
    assert "KIP_OPENAI_API_KEY" in serialized


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
        ontology_reviewers=["knowledge-owner@example.invalid"],
    )
