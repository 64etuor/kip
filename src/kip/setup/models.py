from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kip.domain.json_types import JsonObject
from kip.errors import ValidationError
from kip.setup.paths import canonical_source_root

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_SECRET_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_./:@-]+$")
_DEFAULT_EXTENSIONS = [
    ".csv",
    ".docx",
    ".hwp",
    ".hwpx",
    ".md",
    ".pdf",
    ".txt",
    ".xlsm",
    ".xlsx",
]
_DEFAULT_EXCLUDES = ["**/.DS_Store", "**/.git/**", "**/.~lock.*", "**/~$*"]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecretReference(StrictModel):
    scheme: Literal["env", "keychain", "secret-manager"]
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _SECRET_NAME_PATTERN.fullmatch(value):
            raise ValueError("secret reference name contains invalid characters")
        return value

    @classmethod
    def parse(cls, value: str) -> Self:
        scheme, separator, name = value.partition(":")
        if not separator:
            raise ValueError(
                "secret must be a reference such as env:KIP_DATABASE_URL"
            )
        return cls.model_validate({"scheme": scheme, "name": name})

    def display(self) -> str:
        return f"{self.scheme}:{self.name}"


class FilesystemSourceAnswer(StrictModel):
    name: str
    root: str
    classification: Literal[
        "public", "internal", "confidential", "restricted", "personal"
    ]
    acl_scope: str = Field(min_length=1)
    include_extensions: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_EXTENSIONS),
        min_length=1,
    )
    exclude_globs: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_EXCLUDES)
    )
    read_only: Literal[True] = True
    follow_symlinks: Literal[False] = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _SLUG_PATTERN.fullmatch(value):
            raise ValueError("source name must be a lowercase kebab-case slug")
        return value

    @field_validator("include_extensions")
    @classmethod
    def normalize_extensions(cls, values: list[str]) -> list[str]:
        normalized = [
            value.lower() if value.startswith(".") else f".{value.lower()}"
            for value in values
        ]
        return sorted(set(normalized))

    @classmethod
    def from_user_value(
        cls,
        value: JsonObject,
        *,
        project_root: Path,
    ) -> Self:
        root_value = value.get("root")
        if not isinstance(root_value, str):
            raise ValueError("source root must be a path string")
        root = canonical_source_root(root_value, project_root=project_root)
        normalized = dict(value)
        normalized["root"] = str(root)
        return cls.model_validate(normalized)


class SetupAnswers(StrictModel):
    schema_version: Literal["kip.setup-answers.v1"] = "kip.setup-answers.v1"
    workspace: str | None = None
    identity_mode: Literal["proxy_jwt", "api_key"] | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_jwks_url: str | None = None
    jwt_admin_groups: list[str] | None = None
    identity_api_key_secret_ref: SecretReference | None = None
    identity_admin_key_secret_ref: SecretReference | None = None
    identity_owner: str | None = None
    source_ownership: Literal["company", "personal"] | None = None
    ontology_profile: Literal["empty", "research-project"] | None = None
    filesystem_sources: list[FilesystemSourceAnswer] | None = None
    model_provider: Literal["disabled", "local", "openai", "anthropic"] | None = None
    model_egress_classifications: list[
        Literal["public", "internal", "confidential", "restricted", "personal"]
    ] | None = None
    model_retention_policy: Literal["provider_default", "zero_retention"] | None = None
    model_secret_ref: SecretReference | None = None
    database_secret_ref: SecretReference | None = None
    cas_path: str | None = None
    backup_path: str | None = None
    retention_days: int | None = Field(default=None, ge=1, le=36500)
    sync_schedule: str | None = None
    evaluation_dataset: str | None = None
    interaction_memory_mode: Literal["disabled", "explicit_consent"] | None = None
    ontology_reviewers: list[str] | None = None

    @field_validator("workspace")
    @classmethod
    def validate_workspace(cls, value: str | None) -> str | None:
        if value is not None and not _SLUG_PATTERN.fullmatch(value):
            raise ValueError("workspace must be a lowercase kebab-case slug")
        return value

    @field_validator("jwt_issuer", "jwt_jwks_url")
    @classmethod
    def validate_identity_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback))
        ):
            raise ValueError("identity URL must use HTTPS or loopback HTTP")
        return value

    @field_validator("jwt_audience")
    @classmethod
    def validate_jwt_audience(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("JWT audience cannot be blank")
        return value

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


class SetupQuestion(StrictModel):
    id: str
    prompt: str
    answer_format: str
    choices: list[str] = Field(default_factory=list)
    example: str | None = None
    why: str


class SourceInventory(StrictModel):
    root: str
    file_count: int = 0
    byte_count: int = 0
    extension_counts: dict[str, int] = Field(default_factory=dict)
    excluded_count: int = 0
    symlink_count: int = 0
    unreadable_count: int = 0


class SetupInspection(StrictModel):
    schema_version: Literal["kip.setup-inspection.v1"] = (
        "kip.setup-inspection.v1"
    )
    complete: bool
    answers_fingerprint: str
    questions: list[SetupQuestion] = Field(max_length=1)
    risks: list[str] = Field(default_factory=list)


class SourcePlan(StrictModel):
    name: str
    host_root: str
    target_root: str
    classification: str
    acl_scope: str
    include_extensions: list[str]
    exclude_globs: list[str]
    inventory: SourceInventory


class MountPlan(StrictModel):
    source: str
    target: str
    read_only: bool
    purpose: Literal["source", "cas", "backup"]


class SetupPlan(StrictModel):
    schema_version: Literal["kip.setup-plan.v1"] = "kip.setup-plan.v1"
    plan_fingerprint: str
    answers_fingerprint: str
    workspace: str
    identity_mode: Literal["proxy_jwt", "api_key"]
    jwt_issuer: str | None
    jwt_audience: str | None
    jwt_jwks_url: str | None
    jwt_admin_groups: list[str]
    identity_api_key_secret_ref: SecretReference | None
    identity_admin_key_secret_ref: SecretReference | None
    identity_owner: str
    source_ownership: Literal["company", "personal"]
    ontology_profile: Literal["empty", "research-project"]
    sources: list[SourcePlan]
    mounts: list[MountPlan]
    model_provider: Literal["disabled", "local", "openai", "anthropic"]
    model_egress_classifications: list[str]
    model_retention_policy: Literal["provider_default", "zero_retention"] | None
    model_secret_ref: SecretReference | None
    database_secret_ref: SecretReference
    cas_path: str
    backup_path: str
    retention_days: int
    sync_schedule: str
    evaluation_dataset: str
    interaction_memory_mode: Literal["disabled", "explicit_consent"]
    ontology_reviewers: list[str]
    generated_files: list[str]
    warnings: list[str] = Field(default_factory=list)

    def calculate_fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"plan_fingerprint"},
        )
        return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()

    def verify_fingerprint(self) -> None:
        if self.plan_fingerprint != self.calculate_fingerprint():
            raise ValidationError("setup plan fingerprint does not match its contents")


class SetupApplyReceipt(StrictModel):
    schema_version: Literal["kip.setup-apply.v1"] = "kip.setup-apply.v1"
    plan_fingerprint: str
    written_files: list[str]
    previous_files: list[str]


class SetupCheck(StrictModel):
    name: str
    ok: bool
    detail: str


class SetupReceipt(StrictModel):
    schema_version: Literal["kip.setup-receipt.v1"] = "kip.setup-receipt.v1"
    plan_fingerprint: str
    verified: bool
    checks: list[SetupCheck]
    source_summaries: list[JsonObject]
    limitations: list[str] = Field(default_factory=list)
