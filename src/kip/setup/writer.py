from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import tomli_w
import yaml

from kip.errors import ConflictError
from kip.setup.models import SetupApplyReceipt, SetupPlan
from kip.setup.paths import canonical_managed_path


def apply_setup_plan(
    plan: SetupPlan,
    *,
    project_root: Path,
) -> SetupApplyReceipt:
    try:
        plan.verify_fingerprint()
    except Exception as exc:
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
    config = _config_payload(plan)
    compose = _compose_payload(plan)
    return {
        "config/kip.generated.toml": tomli_w.dumps(config),
        "compose.generated.yaml": yaml.safe_dump(
            compose,
            allow_unicode=True,
            sort_keys=False,
        ),
    }


def _config_payload(plan: SetupPlan) -> dict[str, object]:
    database: dict[str, object] = {
        "statement_timeout_ms": 15000,
        "secret_ref": plan.database_secret_ref.display(),
    }
    if plan.database_secret_ref.scheme == "env":
        database["url_env"] = plan.database_secret_ref.name
    model: dict[str, object] = {
        "enabled": plan.model_provider != "disabled",
        "provider": plan.model_provider,
        "allowed_classifications": plan.model_egress_classifications,
        "retention_policy": plan.model_retention_policy or "provider_default",
    }
    if plan.model_provider == "local":
        model["base_url"] = "http://127.0.0.1:7998"
    if plan.model_secret_ref is not None:
        model["secret_ref"] = plan.model_secret_ref.display()
        if plan.model_secret_ref.scheme == "env":
            model["api_key_env"] = plan.model_secret_ref.name
    sources = [
        {
            "name": source.name,
            "root": source.target_root,
            "enabled": True,
            "read_only": True,
            "follow_symlinks": False,
            "include_extensions": source.include_extensions,
            "exclude_globs": source.exclude_globs,
            "acl_scope": source.acl_scope,
            "classification": source.classification,
        }
        for source in plan.sources
    ]
    identity: dict[str, object] = {
        "mode": plan.identity_mode,
        "owner": plan.identity_owner,
    }
    if plan.identity_mode == "proxy_jwt":
        identity["jwt"] = {
            "issuer": plan.jwt_issuer,
            "audience": plan.jwt_audience,
            "jwks_url": plan.jwt_jwks_url,
            "algorithms": ["RS256"],
            "principal_claim": "sub",
            "workspace_claim": "workspace",
            "group_claim": "groups",
            "scope_claim": "acl_scopes",
            "group_scope_prefix": "group:",
            "admin_groups": plan.jwt_admin_groups,
            "snapshot_id_claim": "acl_snapshot_id",
            "snapshot_version_claim": "acl_snapshot_version",
            "snapshot_captured_at_claim": "acl_snapshot_captured_at",
            "snapshot_expires_at_claim": "acl_snapshot_expires_at",
            "jwks_cache_seconds": 300,
            "jwks_timeout_seconds": 5,
            "clock_skew_seconds": 30,
        }
    else:
        api_key: dict[str, object] = {
            "principal_id": "bootstrap-operator",
            "acl_scopes": [f"workspace:{plan.workspace}"],
        }
        if plan.identity_api_key_secret_ref is not None:
            api_key["secret_ref"] = plan.identity_api_key_secret_ref.display()
            if plan.identity_api_key_secret_ref.scheme == "env":
                api_key["api_key_env"] = plan.identity_api_key_secret_ref.name
        if plan.identity_admin_key_secret_ref is not None:
            api_key["admin_secret_ref"] = plan.identity_admin_key_secret_ref.display()
            if plan.identity_admin_key_secret_ref.scheme == "env":
                api_key["admin_key_env"] = plan.identity_admin_key_secret_ref.name
        identity["api_key"] = api_key
    return {
        "app": {
            "environment": "production",
            "workspace": plan.workspace,
            "log_level": "INFO",
        },
        "setup": {
            "plan_fingerprint": plan.plan_fingerprint,
            "source_ownership": plan.source_ownership,
        },
        "database": database,
        "storage": {"cas_path": "/var/lib/kip/cas"},
        "api": {
            "host": "0.0.0.0",
            "port": 8080,
            "require_api_key_outside_development": True,
            "max_request_bytes": 10 * 1024 * 1024,
        },
        "identity": identity,
        "security": {
            "allow_remote_model_egress": plan.model_provider != "disabled",
            "follow_symlinks": False,
        },
        "search": {
            "semantic_enabled": False,
            "default_mode": "lexical",
            "context_max_chars": 40000,
        },
        "models": {"generation": model},
        "sources": {"filesystem": sources},
        "operations": {
            "backup_path": "/var/lib/kip/backups",
            "retention_days": plan.retention_days,
            "sync_schedule": plan.sync_schedule,
        },
        "evaluation": {"dataset": plan.evaluation_dataset},
        "ontology": {"reviewers": plan.ontology_reviewers},
    }


def _compose_payload(plan: SetupPlan) -> dict[str, object]:
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


def _compose_service(
    plan: SetupPlan,
    environment: dict[str, str],
) -> dict[str, object]:
    return {
        "environment": dict(environment),
        "volumes": [
            {
                "type": "bind",
                "source": mount.source,
                "target": mount.target,
                "read_only": mount.read_only,
            }
            for mount in plan.mounts
        ],
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
