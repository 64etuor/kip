from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from kip.setup.models import (
    FilesystemSourceAnswer,
    SecretReference,
    SetupAnswers,
)


def test_setup_answers_reject_secret_values_and_unknown_fields() -> None:
    with pytest.raises(PydanticValidationError):
        SecretReference.model_validate("sk-live-secret")
    with pytest.raises(PydanticValidationError):
        SetupAnswers.model_validate({"workspace": "acme", "api_key": "secret"})


def test_secret_reference_accepts_env_and_file_schemes() -> None:
    env_reference = SecretReference.parse("env:KIP_DATABASE_URL")
    file_reference = SecretReference.parse("file:/run/secrets/kip-model-key")

    assert env_reference.display() == "env:KIP_DATABASE_URL"
    assert file_reference.display() == "file:/run/secrets/kip-model-key"


@pytest.mark.parametrize("scheme", ["keychain", "secret-manager"])
def test_secret_reference_rejects_unresolvable_schemes_with_guidance(
    scheme: str,
) -> None:
    with pytest.raises(ValueError, match="env:NAME or file:/absolute/path"):
        SecretReference.parse(f"{scheme}:kip/openai")


def test_secret_reference_validates_scheme_shape() -> None:
    with pytest.raises(PydanticValidationError, match="environment variable"):
        SecretReference.parse("env:not/a/variable")
    with pytest.raises(PydanticValidationError, match="absolute path"):
        SecretReference.parse("file:relative/path")


def test_setup_answers_round_trip_uses_versioned_contract() -> None:
    answers = SetupAnswers(
        workspace="acme-rnd",
        database_secret_ref=SecretReference.parse("env:KIP_DATABASE_URL"),
    )

    restored = SetupAnswers.model_validate_json(answers.model_dump_json())

    assert restored == answers
    assert restored.schema_version == "kip.setup-answers.v1"
    assert "postgresql://" not in restored.model_dump_json()


def test_setup_answers_reject_relation_mining_without_generation() -> None:
    # Given relation mining enabled while generation is disabled
    payload = {
        "model_provider": "disabled",
        "relation_mining_mode": "enabled",
    }

    # When/Then the setup boundary rejects the unrunnable combination
    with pytest.raises(PydanticValidationError, match=r"requires.*model provider"):
        SetupAnswers.model_validate(payload)


def test_source_answer_requires_canonical_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    answer = FilesystemSourceAnswer.from_user_value(
        {
            "name": "company-docs",
            "root": str(source),
            "classification": "internal",
            "acl_scope": "workspace:acme-rnd",
        },
        project_root=tmp_path / "project",
    )

    assert answer.root == str(source.resolve())
    assert answer.read_only is True
    assert ".pptx" in answer.include_extensions
    assert json.loads(answer.model_dump_json())["root"] == str(source.resolve())


@pytest.mark.parametrize("unsafe_root", [Path("/"), Path.home()])
def test_source_answer_rejects_excessive_roots(
    tmp_path: Path,
    unsafe_root: Path,
) -> None:
    with pytest.raises(ValueError, match="too broad"):
        FilesystemSourceAnswer.from_user_value(
            {
                "name": "unsafe",
                "root": str(unsafe_root),
                "classification": "internal",
                "acl_scope": "workspace:acme-rnd",
            },
            project_root=tmp_path,
        )
