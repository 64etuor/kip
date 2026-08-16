from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import tomli_w
import yaml

from kip.domain.json_types import JsonObject, JsonValue
from kip.errors import ConflictError, ValidationError
from kip.setup.config_payload import build_config_payload
from kip.setup.models import SetupApplyReceipt, SetupPlan
from kip.setup.paths import canonical_managed_path


def apply_setup_plan(
    plan: SetupPlan,
    *,
    project_root: Path,
) -> SetupApplyReceipt:
    try:
        plan.verify_fingerprint()
    except ValidationError as exc:
        raise ConflictError("setup plan fingerprint is stale or invalid") from exc

    source_roots = [Path(source.host_root) for source in plan.sources]
    try:
        cas_path = canonical_managed_path(
            plan.cas_path,
            project_root=project_root,
            source_roots=source_roots,
        )
        backup_path = canonical_managed_path(
            plan.backup_path,
            project_root=project_root,
            source_roots=source_roots,
            other_managed_paths=[cas_path],
        )
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc
    cas_path.mkdir(parents=True, mode=0o700, exist_ok=True)
    backup_path.mkdir(parents=True, mode=0o700, exist_ok=True)

    files = _render_files(plan)
    written: list[str] = []
    previous: list[str] = []
    for relative, content in files.items():
        target = (project_root / relative).resolve()
        if not target.is_relative_to(project_root.resolve()):
            raise ConflictError(f"generated path escapes project root: {relative}")
        if target.exists():
            backup = target.with_name(f"{target.name}.previous")
            _atomic_copy(target, backup)
            previous.append(str(backup.relative_to(project_root)))
        _atomic_write(target, content)
        written.append(str(target.relative_to(project_root)))
    return SetupApplyReceipt(
        plan_fingerprint=plan.plan_fingerprint,
        written_files=written,
        previous_files=previous,
    )


def atomic_write_json(path: Path, content: str) -> None:
    _atomic_write(path, content)


def _render_files(plan: SetupPlan) -> dict[str, str]:
    config = build_config_payload(plan, container=True)
    host_config = build_config_payload(plan, container=False)
    compose = _compose_payload(plan)
    mcp = _mcp_payload(plan)
    return {
        "config/kip.generated.toml": tomli_w.dumps(config),
        "config/kip.host.generated.toml": tomli_w.dumps(host_config),
        "compose.generated.yaml": yaml.safe_dump(
            compose,
            allow_unicode=True,
            sort_keys=False,
        ),
        ".mcp.json": json.dumps(
            mcp,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    }


def _compose_payload(plan: SetupPlan) -> JsonObject:
    environment = {
        "KIP_CONFIG": "/app/config/kip.generated.toml",
        "KIP_ENV": "production",
        "KIP_WORKSPACE": plan.workspace,
        "KIP_CAS_PATH": "/var/lib/kip/cas",
    }
    if plan.database_secret_ref.scheme == "env":
        environment[plan.database_secret_ref.name] = (
            f"${{{plan.database_secret_ref.name}:?required}}"
        )
    if plan.model_secret_ref and plan.model_secret_ref.scheme == "env":
        environment[plan.model_secret_ref.name] = (
            f"${{{plan.model_secret_ref.name}:?required}}"
        )
    for secret_ref in (
        plan.identity_api_key_secret_ref,
        plan.identity_admin_key_secret_ref,
    ):
        if secret_ref is not None and secret_ref.scheme == "env":
            environment[secret_ref.name] = f"${{{secret_ref.name}:?required}}"
    return {
        "services": {
            "api": _compose_service(plan, environment),
            "worker": _compose_service(plan, environment),
        }
    }


def _mcp_payload(plan: SetupPlan) -> JsonObject:
    return {
        "mcpServers": {
            "kip": {
                "command": "bash",
                "args": ["scripts/mcp.sh"],
                "env": {
                    "KIP_CONFIG": "config/kip.host.generated.toml",
                    "KIP_WORKSPACE": plan.workspace,
                },
            }
        }
    }


def _compose_service(
    plan: SetupPlan,
    environment: dict[str, str],
) -> JsonObject:
    volumes: list[JsonValue] = [
        {
            "type": "bind",
            "source": "./config/kip.generated.toml",
            "target": "/app/config/kip.generated.toml",
            "read_only": True,
        }
    ]
    volumes.extend(
        {
            "type": "bind",
            "source": mount.source,
            "target": mount.target,
            "read_only": mount.read_only,
        }
        for mount in plan.mounts
    )
    return {
        "environment": dict(environment),
        "volumes": volumes,
    }


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        shutil.copy2(source, temporary)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
