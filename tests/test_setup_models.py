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


def test_setup_answers_round_trip_uses_versioned_contract() -> None:
    answers = SetupAnswers(
        workspace="acme-rnd",
        database_secret_ref=SecretReference.parse("env:KIP_DATABASE_URL"),
    )

    restored = SetupAnswers.model_validate_json(answers.model_dump_json())

    assert restored == answers
    assert restored.schema_version == "kip.setup-answers.v1"
    assert "postgresql://" not in restored.model_dump_json()


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
