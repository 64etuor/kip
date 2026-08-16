from __future__ import annotations

from pathlib import Path

from kip.setup.models import (
    FilesystemSourceAnswer,
    SecretReference,
    SetupAnswers,
)


def complete_setup_answers(tmp_path: Path) -> SetupAnswers:
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
