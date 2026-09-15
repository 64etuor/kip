from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import yaml
from pydantic import TypeAdapter

from kip.domain.json_types import JsonObject, JsonValue
from kip.errors import ConfigurationError, ConflictError, ValidationError
from kip.settings import Settings
from kip.setup.config_payload import build_config_payload
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
from kip.setup.paths import canonical_managed_path, canonical_source_root
from kip.setup.planner import build_setup_plan, inspect_setup
from kip.setup.writer import apply_setup_plan, atomic_write_json, build_compose_payload

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
                    "local_file_count": source.inventory.local_file_count,
                    "cloud_placeholder_count": source.inventory.cloud_placeholder_count,
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
            "until ./scripts/app-up.sh --database-only and a source sync have completed. "
            "For the API and worker, run ./scripts/app-up.sh"
        )
        first_source = plan.sources[0].name if plan.sources else "SOURCE"
        next_steps = [
            "./scripts/app-up.sh --database-only",
            f"./scripts/kip sync run --source {first_source}",
            './scripts/kip search "smoke test query" --limit 5',
            "./scripts/kip read UNIT_ID",
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
        docker_required = plan.database_secret_ref.name == "KIP_DATABASE_URL"
        docker_cli = shutil.which("docker") if docker_required else None
        checks.append(
            SetupCheck(
                name="docker_cli",
                ok=not docker_required or docker_cli is not None,
                detail=(
                    "not required for external-database CLI/MCP; the optional API/worker profile still needs Docker"
                    if not docker_required else docker_cli
                    if docker_cli
                    else "docker CLI not found; run ./scripts/bootstrap.sh --install-docker"
                ),
            )
        )
        if docker_cli:
            checks.append(_docker_daemon_check(docker_cli))
            checks.append(_compose_isolation_check(docker_cli, self.project_root))
        checks.append(_database_secret_check(plan))
        if plan.identity_mode == "api_key":
            for name, reference in (
                ("identity_api_key_secret", plan.identity_api_key_secret_ref),
                ("identity_admin_key_secret", plan.identity_admin_key_secret_ref),
            ):
                if reference is not None:
                    checks.append(_secret_check(name, reference))
            checks.append(_identity_key_separation_check(plan))
        if plan.model_provider in {"openai", "anthropic"} and plan.model_secret_ref:
            checks.append(_secret_check("model_secret", plan.model_secret_ref))
        if plan.model_provider == "local":
            checks.append(SetupCheck(
                name="local_generation_service",
                ok=False,
                detail=(
                    "local generation is not installed by setup; provide and verify a "
                    "generation service reachable from the selected runtime before "
                    "enabling model operations"
                ),
            ))
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
            if source.inventory.cloud_placeholder_count:
                checks.append(SetupCheck(
                    name=f"source_local_files:{source.name}",
                    ok=source.inventory.local_file_count > 0,
                    detail=(
                        f"plan preview found {source.inventory.local_file_count} local and "
                        f"{source.inventory.cloud_placeholder_count} cloud-only files; "
                        "download chosen files in OneDrive (or the cloud provider), then "
                        "rerun setup preview before sync; indexing never downloads placeholders"
                    ),
                ))
        return checks

    def _parse_answer(
        self,
        question_id: str,
        value: str,
        answers: SetupAnswers,
    ) -> JsonValue | SecretReference | list[FilesystemSourceAnswer]:
        if question_id == "filesystem_sources":
            objects: list[JsonObject]
            if value.lstrip().startswith("["):
                parsed_sources = _load_json(value)
            else:
                parsed_sources = [value.strip()]
            if isinstance(parsed_sources, list) and all(
                isinstance(item, str) for item in parsed_sources
            ):
                objects = []
                for item in parsed_sources:
                    root = Path(str(item)).expanduser()
                    if not root.is_absolute():
                        raise ValidationError("source folders must be absolute paths")
                    canonical = canonical_source_root(
                        str(root), project_root=self.project_root,
                    )
                    slug = re.sub(r"[^a-z0-9]+", "-", canonical.name.lower())
                    slug = slug.strip("-")[:45] or "folder"
                    suffix = hashlib.sha256(str(canonical).encode()).hexdigest()[:10]
                    objects.append({
                        "name": f"{slug}-{suffix}",
                        "root": str(canonical),
                        "classification": (
                            "personal" if answers.source_ownership == "personal" else "restricted"
                        ),
                        "acl_scope": f"workspace:{answers.workspace}",
                    })
            else:
                objects = _JSON_OBJECTS.validate_python(parsed_sources)
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
                config == build_config_payload(plan, container=True)
                and host_config == build_config_payload(plan, container=False)
                and compose.get("x-kip-setup") == {
                    "mode": "standalone", "plan_fingerprint": plan.plan_fingerprint,
                }
                and compose == build_compose_payload(plan, project_root=self.project_root)
            )
            read_only_ok = _all_source_mounts_read_only(compose, plan)
            mcp_ok = _mcp_uses_generated_config(
                mcp_value, plan, project_root=self.project_root
            )
        except (OSError, ValueError, TypeError, ValidationError, yaml.YAMLError):
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


def _mcp_uses_generated_config(value: object, plan: SetupPlan, *, project_root: Path) -> bool:
    # Setup before 3.15.1 wrote relative paths and upgrades preserve `.mcp.json`,
    # so both forms select the generated config. `kip doctor` reports the
    # relative form as `mcp_registration`; verify does not fail it.
    if not isinstance(value, dict):
        return False
    servers = value.get("mcpServers")
    server = servers.get("kip") if isinstance(servers, dict) else None
    if not isinstance(server, dict):
        return False
    environment = server.get("env", {})
    if not isinstance(environment, dict):
        return False
    command = server.get("command")
    root = project_root.resolve()
    if isinstance(command, str) and server.get("args") == ["mcp"] and (
        command == "kip" or Path(command).resolve() == root / "scripts/kip"
    ):
        # The launcher form doctor suggests: `scripts/kip` selects the
        # generated host config itself, so only a pinned root, config or
        # workspace must still name this plan. A path to any other `kip`
        # cannot be attributed to this deployment.
        pinned_roots = {environment.get("KIP_PROJECT_ROOT"), environment.get("KIP_HOME")} - {None}
        return (
            all(isinstance(value, str) and Path(value).resolve() == root for value in pinned_roots)
            and environment.get("KIP_WORKSPACE", plan.workspace) == plan.workspace
            and environment.get("KIP_CONFIG") in {None, str(root / "config/kip.host.generated.toml")}
        )
    if environment.get("KIP_WORKSPACE") != plan.workspace:
        return False
    root = project_root.resolve()
    forms = (
        (["scripts/mcp.sh"], "config/kip.host.generated.toml"),
        ([str(root / "scripts/mcp.sh")], str(root / "config/kip.host.generated.toml")),
    )
    return server.get("command") == "bash" and any(
        server.get("args") == args and environment.get("KIP_CONFIG") == config
        for args, config in forms
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


_COMPOSE_PS_FORMAT = (
    '{{.Label "com.docker.compose.project"}}\t'
    '{{.Label "com.docker.compose.project.working_dir"}}\t{{.Ports}}'
)
# Compose labels a volume with its project but not with the directory that
# created it, so a volume is attributed only through its project's containers.
_COMPOSE_VOLUME_FORMAT = '{{.Label "com.docker.compose.project"}}\t{{.Name}}'


def _compose_isolation_check(docker_cli: str, project_root: Path) -> SetupCheck:
    """Whether this deployment's Compose project and host ports are its own.

    Compose keys containers and volumes by project name, and every deployment's
    Compose file names its project `kip`, so a second deployment reuses the
    first one's database unless its `.env` says otherwise. `app-up.sh` refuses
    a shared project; this reports it, another deployment's stopped volumes,
    and a taken port, before that.
    """
    name = "compose_project_isolation"
    root = project_root.resolve()
    dotenv_path = root / ".env"
    try:
        dotenv = _dotenv_assignments(root)
    except ValueError as exc:
        return SetupCheck(
            name=name, ok=False,
            detail=f"cannot read {dotenv_path} the way scripts/common.sh does: {exc}",
        )

    def setting(key: str) -> str:
        # scripts/common.sh exports .env beneath the caller's own exports.
        return os.environ.get(key) or dotenv.get(key, "")

    project = setting("COMPOSE_PROJECT_NAME")
    if not project:
        project, problem = _compose_file_project(root)
        if problem:
            return SetupCheck(name=name, ok=False, detail=problem)
    ports: dict[str, int] = {}
    for key, default in (("KIP_POSTGRES_PORT", "5432"), ("KIP_API_PORT", "8080")):
        value = setting(key) or default
        if not (value.isascii() and value.isdigit() and 1 <= int(value) <= 65535):
            return SetupCheck(
                name=name, ok=False,
                detail=f"{key}={value!r} is not a port; set an integer from 1 to 65535 in {dotenv_path}",
            )
        ports[key] = int(value)
    containers = _docker_rows(docker_cli, ["ps", "--all", "--format", _COMPOSE_PS_FORMAT], 3)
    if containers is None:
        return SetupCheck(name=name, ok=True, detail="not checked: Docker could not be queried")
    volumes = _docker_rows(docker_cli, ["volume", "ls", "--format", _COMPOSE_VOLUME_FORMAT], 2) or []
    problems: list[str] = []
    fixes: list[str] = []
    mine = any(
        label == project and _same_directory(directory, root) for label, directory, _ in containers
    )
    others = sorted({
        directory for label, directory, _ in containers
        if label == project and directory and not _same_directory(directory, root)
    })
    project_volumes = sorted(volume for label, volume in volumes if label == project)
    if others:
        problems.append(
            f'Docker Compose project "{project}" already has containers from {", ".join(others)}'
        )
    elif project_volumes and not mine:
        problems.append(
            f'Docker Compose project "{project}" has volumes ({", ".join(project_volumes)}) but no '
            "containers, and Docker does not record which directory created them; unless this "
            "deployment created them and was stopped with ./scripts/app-up.sh --down, they hold "
            "another deployment's database, which this deployment's password cannot open"
        )
    if problems:
        fixes.append(
            f"set a unique COMPOSE_PROJECT_NAME in {dotenv_path} for a new, empty database, or, if "
            "this is the same deployment moved from another directory, run KIP_COMPOSE_ADOPT=1 "
            "./scripts/app-up.sh --down once"
        )
    for key, port in ports.items():
        if not _port_listening(port):
            continue
        holders = [(label, directory) for label, directory, published in containers
                   if _publishes(published, port)]
        if any(label == project and _same_directory(directory, root) for label, directory in holders):
            continue
        holder = next(
            (f'a container of Compose project "{label}" at {directory}'
             for label, directory in holders if label),
            "a Docker container outside Compose" if holders
            else "a process that is not this deployment's container",
        )
        problems.append(f"127.0.0.1:{port} ({key}) is held by {holder}")
        if key == "KIP_POSTGRES_PORT":
            fixes.append(
                "set a free KIP_POSTGRES_PORT and the same port in KIP_DATABASE_URL and "
                "KIP_BACKUP_DATABASE_URL"
            )
        else:
            fixes.append(
                "only the app profile publishes the API (./scripts/app-up.sh without "
                "--database-only), so before starting it set a free KIP_API_PORT"
            )
    if not problems:
        return SetupCheck(
            name=name, ok=True,
            detail=(
                f'Compose project "{project}" and 127.0.0.1:{ports["KIP_POSTGRES_PORT"]}/'
                f'{ports["KIP_API_PORT"]} belong to this deployment or are free'
            ),
        )
    return SetupCheck(
        name=name, ok=False,
        detail=(
            "; ".join(problems) + ". Before ./scripts/app-up.sh: " + "; ".join(fixes)
            + ". Leave KIP_SEMANTIC_PORT on the running model runtime (docs/OPERATIONS.md)"
        ),
    )


def _docker_rows(docker_cli: str, arguments: list[str], fields: int) -> list[list[str]] | None:
    """Tab-separated `docker ... --format` rows, or None when Docker cannot be queried."""
    try:
        completed = subprocess.run(
            [docker_cli, *arguments], capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    rows = []
    for line in completed.stdout.splitlines():
        if line.strip():
            cells = line.split("\t", fields - 1)
            rows.append(cells + [""] * (fields - len(cells)))
    return rows


def _dotenv_assignments(root: Path) -> dict[str, str]:
    """`.env` read by the deployment's own scripts/load_dotenv.py, as common.sh reads it."""
    path = root / ".env"
    if not path.is_file():
        return {}
    loader = root / "scripts/load_dotenv.py"
    spec = importlib.util.spec_from_file_location("kip_deployment_load_dotenv", loader)
    if not loader.is_file() or spec is None or spec.loader is None:
        raise ValueError(f"{loader} is missing")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return dict(module.parse_dotenv(path))
    except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc


def _compose_file_project(root: Path) -> tuple[str, str]:
    """The project name Compose resolves without COMPOSE_PROJECT_NAME, or a problem."""
    # The Compose file app-up.sh selects: generated after setup apply.
    for filename in ("compose.generated.yaml", "compose.yaml"):
        path = root / filename
        if not path.is_file():
            continue
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            document, error = None, f"{exc.__class__.__name__}"
        else:
            error = "" if isinstance(document, dict) else "not a Compose mapping"
        if error:
            return "", (
                f"cannot read the Compose project name from {path} ({error}); fix the file or "
                f"set COMPOSE_PROJECT_NAME in {root / '.env'}"
            )
        assert isinstance(document, dict)
        value = document.get("name")
        if isinstance(value, str) and "$" in value:
            return "", (
                f"{filename} sets the Compose project name by interpolation (name: {value}), "
                f"which setup verify does not resolve; set COMPOSE_PROJECT_NAME in {root / '.env'} "
                "to the resolved name"
            )
        if isinstance(value, str) and value:
            return value, ""
        break
    return re.sub(r"[^a-z0-9_-]", "", root.name.lower()).lstrip("_-"), ""


def _same_directory(value: str, root: Path) -> bool:
    try:
        return bool(value) and os.path.samefile(value, root)
    except OSError:
        return False


def _publishes(published: str, port: int) -> bool:
    return any(
        int(first) <= port <= int(last or first)
        for first, last in re.findall(r":(\d+)(?:-(\d+))?->", published)
    )


def _port_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _database_secret_check(plan: SetupPlan) -> SetupCheck:
    return _secret_check("database_secret", plan.database_secret_ref, database=True)


def _secret_check(
    name: str, reference: SecretReference, *, database: bool = False,
) -> SetupCheck:
    try:
        value = Settings.for_test().resolve_secret_reference(reference.display())
        if any(marker in value for marker in ("change-me-before-use", "replace-with-")):
            raise ConfigurationError("replace the example credential before starting the deployment")
        if database:
            parsed = urlsplit(value)
            if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
                raise ConfigurationError("a PostgreSQL connection URL is required")
        return SetupCheck(name=name, ok=True, detail="resolvable")
    except (ConfigurationError, ValueError):
        return SetupCheck(
            name=name,
            ok=False,
            detail=(
                f"set a valid non-placeholder {reference.display()} credential; "
                "secret files must be readable, regular, and single-line"
            ),
        )


def _identity_key_separation_check(plan: SetupPlan) -> SetupCheck:
    try:
        resolver = Settings.for_test()
        api_key = (
            resolver.resolve_secret_reference(plan.identity_api_key_secret_ref.display())
            if plan.identity_api_key_secret_ref else ""
        )
        admin_key = (
            resolver.resolve_secret_reference(plan.identity_admin_key_secret_ref.display())
            if plan.identity_admin_key_secret_ref else ""
        )
        distinct = bool(api_key and admin_key and api_key != admin_key)
    except ConfigurationError:
        distinct = False
    return SetupCheck(
        name="identity_key_separation",
        ok=distinct,
        detail=(
            "distinct keys" if distinct else "set different nonempty API and admin credentials"
        ),
    )
