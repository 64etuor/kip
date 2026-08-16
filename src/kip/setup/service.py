from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast

import yaml
from pydantic import TypeAdapter

from kip.domain.json_types import JsonObject, JsonValue
from kip.errors import ConflictError, ValidationError
from kip.setup.inventory import inspect_source
from kip.setup.models import (
    FilesystemSourceAnswer,
    SecretReference,
    SetupAnswers,
    SetupApplyReceipt,
    SetupCheck,
    SetupInspection,
    SetupPlan,
    SetupReceipt,
    SourcePreview,
)
from kip.setup.paths import canonical_managed_path
from kip.setup.planner import build_setup_plan, inspect_setup
from kip.setup.writer import apply_setup_plan, atomic_write_json

_JSON_OBJECTS = TypeAdapter(list[JsonObject])
_STRINGS = TypeAdapter(list[str])


class SetupService:
    def __init__(self, *, project_root: Path, state_path: Path) -> None:
        self.project_root = project_root.resolve()
        self.state_path = state_path.resolve()

    def load_answers(self) -> SetupAnswers:
        if not self.state_path.exists():
            return SetupAnswers()
        try:
            answers = SetupAnswers.model_validate_json(
                self.state_path.read_text(encoding="utf-8")
            )
            return self._revalidate_paths(answers)
        except (OSError, ValueError) as exc:
            raise ValidationError(f"invalid setup state: {self.state_path}") from exc

    def inspect(self) -> SetupInspection:
        return inspect_setup(self.load_answers(), project_root=self.project_root)

    def record_answer(self, question_id: str, value: str) -> SetupInspection:
        answers = self.load_answers()
        inspection = inspect_setup(answers, project_root=self.project_root)
        current = inspection.questions[0].id if inspection.questions else None
        existing = getattr(answers, question_id, None)
        if current != question_id and existing is None:
            raise ConflictError(
                f"expected answer for {current or 'no remaining question'}, not {question_id}"
            )
        parsed = self._parse_answer(question_id, value, answers)
        updated = answers.model_copy(update={question_id: parsed})
        updated = SetupAnswers.model_validate(updated.model_dump(mode="json"))
        atomic_write_json(
            self.state_path,
            updated.model_dump_json(indent=2) + "\n",
        )
        return inspect_setup(updated, project_root=self.project_root)

    def preview(self) -> list[SourcePreview]:
        answers = self.load_answers()
        if answers.filesystem_sources is None:
            raise ValidationError("filesystem_sources must be answered before preview")
        return [
            SourcePreview(
                name=source.name,
                classification=source.classification,
                acl_scope=source.acl_scope,
                **inspect_source(source).model_dump(),
            )
            for source in answers.filesystem_sources
        ]

    def create_plan(self) -> SetupPlan:
        return build_setup_plan(
            self.load_answers(),
            project_root=self.project_root,
        )

    def write_plan(self, output: Path) -> SetupPlan:
        plan = self.create_plan()
        atomic_write_json(output, plan.model_dump_json(indent=2) + "\n")
        return plan

    def load_plan(self, path: Path) -> SetupPlan:
        try:
            plan = SetupPlan.model_validate_json(path.read_text(encoding="utf-8"))
            plan.verify_fingerprint()
            return plan
        except (OSError, ValueError) as exc:
            raise ValidationError(f"invalid setup plan: {path}") from exc

    def apply(self, plan: SetupPlan) -> SetupApplyReceipt:
        current = self.load_answers()
        if plan.answers_fingerprint != current.fingerprint():
            raise ConflictError("setup plan is stale for the current answers")
        return apply_setup_plan(plan, project_root=self.project_root)

    def verify(self, plan: SetupPlan) -> SetupReceipt:
        plan.verify_fingerprint()
        config_path = self.project_root / "config/kip.generated.toml"
        host_config_path = self.project_root / "config/kip.host.generated.toml"
        compose_path = self.project_root / "compose.generated.yaml"
        mcp_path = self.project_root / ".mcp.json"
        checks: list[SetupCheck] = []
        checks.append(_file_check("generated_config", config_path))
        checks.append(_file_check("generated_host_config", host_config_path))
        checks.append(_file_check("compose_override", compose_path))
        checks.append(_file_check("mcp_adapter", mcp_path))
        checks.extend(
            self._parse_checks(
                plan,
                config_path,
                host_config_path,
                compose_path,
                mcp_path,
            )
        )
        source_summaries: list[JsonObject] = []
        for source in plan.sources:
            exists = Path(source.host_root).is_dir()
            checks.append(
                SetupCheck(
                    name=f"source:{source.name}",
                    ok=exists,
                    detail="available" if exists else "unavailable",
                )
            )
            source_summaries.append(
                {
                    "name": source.name,
                    "classification": source.classification,
                    "file_count": source.inventory.file_count,
                    "byte_count": source.inventory.byte_count,
                    "excluded_count": source.inventory.excluded_count,
                }
            )
        limitations = list(plan.warnings)
        if plan.identity_mode == "api_key":
            limitations.append(
                "API key identity is intended for bootstrap only; proxy JWT is recommended"
            )
        limitations.append(
            "setup apply/verify only generate configuration; nothing is indexed "
            "or served until ./scripts/app-up.sh (or the app profile) and a "
            "source sync have completed"
        )
        first_source = plan.sources[0].name if plan.sources else "SOURCE"
        next_steps = [
            "./scripts/migrate.sh",
            "./scripts/app-up.sh",
            f"./scripts/kip sync run --source {first_source}",
            './scripts/kip search "smoke test query" --limit 5',
        ]
        return SetupReceipt(
            plan_fingerprint=plan.plan_fingerprint,
            verified=all(check.ok for check in checks),
            checks=checks,
            runtime_readiness=self._runtime_readiness(plan),
            source_summaries=source_summaries,
            limitations=limitations,
            next_steps=next_steps,
        )

    def _runtime_readiness(self, plan: SetupPlan) -> list[SetupCheck]:
        checks: list[SetupCheck] = []
        python_ok = sys.version_info >= (3, 12)
        checks.append(
            SetupCheck(
                name="python_version",
                ok=python_ok,
                detail=(
                    f"{sys.version_info.major}.{sys.version_info.minor}"
                    if python_ok
                    else (
                        f"{sys.version_info.major}.{sys.version_info.minor} "
                        "is too old; install Python 3.12+ and re-run "
                        "./scripts/bootstrap.sh"
                    )
                ),
            )
        )
        docker_cli = shutil.which("docker")
        checks.append(
            SetupCheck(
                name="docker_cli",
                ok=docker_cli is not None,
                detail=(
                    docker_cli
                    if docker_cli
                    else "docker CLI not found; install Docker to run "
                    "./scripts/app-up.sh"
                ),
            )
        )
        if docker_cli:
            checks.append(_docker_daemon_check(docker_cli))
        checks.append(_database_secret_check(plan))
        for source in plan.sources:
            root = Path(source.host_root)
            readable = root.is_dir() and os.access(root, os.R_OK | os.X_OK)
            checks.append(
                SetupCheck(
                    name=f"source_readable:{source.name}",
                    ok=readable,
                    detail=(
                        "readable"
                        if readable
                        else f"{root} is missing or unreadable; restore the "
                        "mount or fix its permissions before syncing"
                    ),
                )
            )
        return checks

    def _parse_answer(
        self,
        question_id: str,
        value: str,
        answers: SetupAnswers,
    ) -> JsonValue | SecretReference | list[FilesystemSourceAnswer]:
        if question_id == "filesystem_sources":
            objects = _JSON_OBJECTS.validate_python(_load_json(value))
            sources = [
                FilesystemSourceAnswer.from_user_value(
                    item,
                    project_root=self.project_root,
                )
                for item in objects
            ]
            names = [source.name for source in sources]
            if len(names) != len(set(names)):
                raise ValidationError("filesystem source names must be unique")
            return sources
        if question_id in {
            "model_egress_classifications",
            "ontology_reviewers",
            "jwt_admin_groups",
        }:
            values = _STRINGS.validate_python(_load_json(value))
            if question_id == "ontology_reviewers" and not values:
                raise ValidationError("at least one ontology reviewer is required")
            return cast(JsonValue, values)
        if question_id in {
            "model_secret_ref",
            "database_secret_ref",
            "identity_api_key_secret_ref",
            "identity_admin_key_secret_ref",
        }:
            try:
                reference = SecretReference.parse(value)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            if question_id != "model_secret_ref" and reference.scheme != "env":
                raise ValidationError(
                    f"{question_id} must use an env: reference (for example "
                    "env:KIP_DATABASE_URL); the runtime resolves this secret "
                    "only through an environment variable, optionally backed "
                    "by a NAME_FILE indirection"
                )
            return reference
        if question_id == "retention_days":
            try:
                return int(value)
            except ValueError as exc:
                raise ValidationError("retention_days must be an integer") from exc
        if question_id in {"cas_path", "backup_path"}:
            source_roots = [
                Path(source.root)
                for source in answers.filesystem_sources or []
            ]
            other_value = (
                answers.backup_path
                if question_id == "cas_path"
                else answers.cas_path
            )
            other_paths = [Path(other_value)] if other_value else []
            try:
                path = canonical_managed_path(
                    value,
                    project_root=self.project_root,
                    source_roots=source_roots,
                    other_managed_paths=other_paths,
                )
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            return str(path)
        if question_id == "evaluation_dataset":
            if value == "none":
                return value
            path = Path(value).expanduser().resolve()
            if not path.is_file():
                raise ValidationError("evaluation dataset is not an existing file")
            return str(path)
        if question_id == "sync_schedule":
            if value != "manual" and len(value.split()) != 5:
                raise ValidationError("sync_schedule must be manual or a 5-field cron")
            return value
        if question_id == "workspace":
            return value
        if question_id in {
            "identity_owner",
            "jwt_issuer",
            "jwt_audience",
            "jwt_jwks_url",
        }:
            if not value.strip():
                raise ValidationError(f"{question_id} cannot be blank")
            return value.strip()
        if question_id in {
            "identity_mode",
            "source_ownership",
            "ontology_profile",
            "model_provider",
            "model_retention_policy",
            "relation_mining_mode",
            "interaction_memory_mode",
        }:
            return value
        raise ValidationError(f"unknown setup question: {question_id}")

    def _revalidate_paths(self, answers: SetupAnswers) -> SetupAnswers:
        try:
            sources = [
                FilesystemSourceAnswer.from_user_value(
                    cast(JsonObject, source.model_dump(mode="json")),
                    project_root=self.project_root,
                )
                for source in answers.filesystem_sources or []
            ]
            source_roots = [Path(source.root) for source in sources]
            cas = (
                canonical_managed_path(
                    answers.cas_path,
                    project_root=self.project_root,
                    source_roots=source_roots,
                )
                if answers.cas_path
                else None
            )
            backup = (
                canonical_managed_path(
                    answers.backup_path,
                    project_root=self.project_root,
                    source_roots=source_roots,
                    other_managed_paths=[cas] if cas else [],
                )
                if answers.backup_path
                else None
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return answers.model_copy(
            update={
                "filesystem_sources": sources
                if answers.filesystem_sources is not None
                else None,
                "cas_path": str(cas) if cas else None,
                "backup_path": str(backup) if backup else None,
            }
        )

    def _parse_checks(
        self,
        plan: SetupPlan,
        config_path: Path,
        host_config_path: Path,
        compose_path: Path,
        mcp_path: Path,
    ) -> list[SetupCheck]:
        if (
            not config_path.is_file()
            or not host_config_path.is_file()
            or not compose_path.is_file()
            or not mcp_path.is_file()
        ):
            return []
        try:
            with config_path.open("rb") as handle:
                config = tomllib.load(handle)
            with host_config_path.open("rb") as handle:
                host_config = tomllib.load(handle)
            compose_value = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
            compose = compose_value if isinstance(compose_value, dict) else {}
            mcp_value = json.loads(mcp_path.read_text(encoding="utf-8"))
            fingerprint_ok = (
                config.get("setup", {}).get("plan_fingerprint")
                == plan.plan_fingerprint
                and host_config.get("setup", {}).get("plan_fingerprint")
                == plan.plan_fingerprint
            )
            read_only_ok = _all_source_mounts_read_only(compose, plan)
            mcp_ok = _mcp_uses_generated_config(mcp_value, plan)
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            fingerprint_ok = False
            read_only_ok = False
            mcp_ok = False
        return [
            SetupCheck(
                name="plan_fingerprint",
                ok=fingerprint_ok,
                detail="matches" if fingerprint_ok else "mismatch",
            ),
            SetupCheck(
                name="read_only_source_mounts",
                ok=read_only_ok,
                detail="enforced" if read_only_ok else "missing",
            ),
            SetupCheck(
                name="mcp_runtime_config",
                ok=mcp_ok,
                detail="generated config selected" if mcp_ok else "mismatch",
            ),
        ]


def _load_json(value: str) -> JsonValue:
    try:
        parsed: JsonValue = json.loads(value)
        return parsed
    except json.JSONDecodeError as exc:
        raise ValidationError("answer must be valid JSON") from exc


def _file_check(name: str, path: Path) -> SetupCheck:
    return SetupCheck(
        name=name,
        ok=path.is_file(),
        detail="present" if path.is_file() else "missing",
    )


def _all_source_mounts_read_only(
    compose: dict[object, object],
    plan: SetupPlan,
) -> bool:
    services = compose.get("services")
    if not isinstance(services, dict):
        return False
    for service_name in ("api", "worker"):
        service = services.get(service_name)
        if not isinstance(service, dict):
            return False
        volumes = service.get("volumes")
        if not isinstance(volumes, list):
            return False
        for source in plan.sources:
            matches = [
                volume
                for volume in volumes
                if isinstance(volume, dict)
                and volume.get("target") == source.target_root
            ]
            if len(matches) != 1 or matches[0].get("read_only") is not True:
                return False
    return True


def _mcp_uses_generated_config(value: object, plan: SetupPlan) -> bool:
    if not isinstance(value, dict):
        return False
    servers = value.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    server = servers.get("kip")
    if not isinstance(server, dict):
        return False
    environment = server.get("env")
    return bool(
        server.get("command") == "bash"
        and server.get("args") == ["scripts/mcp.sh"]
        and isinstance(environment, dict)
        and environment.get("KIP_CONFIG") == "config/kip.host.generated.toml"
        and environment.get("KIP_WORKSPACE") == plan.workspace
    )


def _docker_daemon_check(docker_cli: str) -> SetupCheck:
    try:
        completed = subprocess.run(
            [docker_cli, "info"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        ok = completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    return SetupCheck(
        name="docker_daemon",
        ok=ok,
        detail=(
            "reachable"
            if ok
            else "docker daemon unreachable; start Docker, then run "
            "./scripts/app-up.sh"
        ),
    )


def _database_secret_check(plan: SetupPlan) -> SetupCheck:
    reference = plan.database_secret_ref
    if reference.scheme == "env":
        resolvable = bool(
            os.environ.get(reference.name)
            or os.environ.get(f"{reference.name}_FILE")
        )
        detail = (
            "resolvable"
            if resolvable
            else f"set {reference.name} (or {reference.name}_FILE) in the "
            "environment or .env before starting the app profile"
        )
    else:
        resolvable = Path(reference.name).is_file()
        detail = (
            "resolvable"
            if resolvable
            else f"secret file {reference.name} is missing or unreadable"
        )
    return SetupCheck(
        name="database_secret",
        ok=resolvable,
        detail=detail,
    )
