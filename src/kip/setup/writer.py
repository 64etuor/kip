from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import tomli_w
import yaml
from pydantic import TypeAdapter

from kip.domain.json_types import JsonObject, JsonValue
from kip.errors import ConflictError, ValidationError
from kip.setup.config_payload import build_config_payload
from kip.setup.models import SetupApplyReceipt, SetupPlan
from kip.setup.paths import canonical_managed_path, validate_container_source_target

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)

MANAGED_ROLES_NOTE = (
    "api and worker connect as the non-superuser, NOBYPASSRLS kip_api and kip_worker "
    "logins; only migrate and the one-shot roles service use the bootstrap owner, "
    "which PostgreSQL creates SUPERUSER with BYPASSRLS. The roles service applies "
    "deploy/sql/roles.sql.template after every migrate run; re-run it after later "
    "migrations. KIP_API_DB_PASSWORD, KIP_WORKER_DB_PASSWORD and KIP_BACKUP_DB_PASSWORD "
    "come from the same .env that carries POSTGRES_PASSWORD (scripts/bootstrap.sh "
    "writes them); setup's secret references cover only the single database URL."
)

EXTERNAL_ROLES_NOTE = (
    "External database: setup neither creates nor owns roles there, so this project has "
    "no roles service. Apply deploy/sql/roles.sql.template yourself as the object owner, "
    "after every migration. migrate, api and worker all read {name}, so that login needs "
    "the rights migrations require and must never be SUPERUSER or BYPASSRLS - either is "
    "enough to bypass every workspace and ACL policy. To isolate api and worker by row "
    "level security, run migrations separately as the owner and point {name} at a login "
    "bound to kip_api / kip_worker. BYPASSRLS is a role attribute and is not inherited "
    "through membership, so back up as a login that has it (kip_backup) rather than a "
    "member of it."
)


def apply_setup_plan(
    plan: SetupPlan,
    *,
    project_root: Path,
) -> SetupApplyReceipt:
    try:
        plan.verify_fingerprint()
    except ValidationError as exc:
        raise ConflictError("setup plan fingerprint is stale or invalid") from exc
    if (
        plan.runtime_uid is None or plan.runtime_gid is None
        or plan.runtime_supplementary_gids is None
    ):
        raise ConflictError("setup plan predates explicit runtime ownership; regenerate and approve a new plan")
    if (plan.runtime_uid, plan.runtime_gid) != (os.getuid(), os.getgid()):
        raise ConflictError("apply setup as the non-root user and group recorded in the plan, or regenerate the plan")
    if not set(plan.runtime_supplementary_gids).issubset(os.getgroups()):
        raise ConflictError("the applying user no longer belongs to the plan's supplementary groups; restore membership or regenerate the plan")
    for source in plan.sources:
        if source.host_root != source.target_root:
            raise ConflictError("setup plan uses different host and container source paths; regenerate the plan")
        try:
            validate_container_source_target(source.target_root)
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

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
    files = _render_files(plan, project_root=project_root)
    cas_path.mkdir(parents=True, mode=0o700, exist_ok=True)
    backup_path.mkdir(parents=True, mode=0o700, exist_ok=True)
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


def _render_files(plan: SetupPlan, *, project_root: Path) -> dict[str, str]:
    config = build_config_payload(plan, container=True)
    host_config = build_config_payload(plan, container=False)
    compose = build_compose_payload(plan, project_root=project_root)
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


def build_compose_payload(plan: SetupPlan, *, project_root: Path) -> JsonObject:
    try:
        base = yaml.safe_load((project_root / "compose.yaml").read_text(encoding="utf-8"))
        services = base["services"]
        if not all(isinstance(services[name], dict) for name in ("api", "worker", "migrate", "postgres")):
            raise ValueError("missing application services")
        # The bundled-database services keep the non-superuser application-role
        # URLs the template already gives them (kip_api / kip_worker) instead of
        # the owner URL below: PostgreSQL creates the bootstrap owner as a
        # SUPERUSER with BYPASSRLS, and a superuser bypasses every workspace and
        # ACL policy, so an API or worker that connects as the owner has no
        # isolation in the database. Reading them here keeps one definition of
        # those URLs, in compose.yaml.
        role_database_urls = {
            name: services[name]["environment"]["KIP_DATABASE_URL"] for name in ("api", "worker")
        }
        if any(
            "${POSTGRES_USER" in url or "${POSTGRES_PASSWORD" in url
            for url in role_database_urls.values()
        ):
            raise ValueError("api and worker template URLs carry the owner credentials")
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise ValidationError("setup requires the package compose.yaml application template") from exc
    environment = {
        "KIP_CONFIG": "/app/config/kip.generated.toml",
        "KIP_ENV": "production",
        "KIP_WORKSPACE": plan.workspace,
        "KIP_CAS_PATH": "/var/lib/kip/cas",
        "KIP_IDENTITY_MODE": plan.identity_mode,
        "KIP_API_PRINCIPAL_ID": "bootstrap-operator",
        "KIP_API_ACL_SCOPES": f"workspace:{plan.workspace}",
        "KIP_JWT_ISSUER": plan.jwt_issuer or "",
        "KIP_JWT_AUDIENCE": plan.jwt_audience or "",
        "KIP_JWT_JWKS_URL": plan.jwt_jwks_url or "",
    }
    managed_database = plan.database_secret_ref.name == "KIP_DATABASE_URL"
    if managed_database:
        environment["KIP_DATABASE_URL"] = (
            "${KIP_CONTAINER_DATABASE_URL:?start the generated profile with ./scripts/app-up.sh}"
        )
    else:
        environment[plan.database_secret_ref.name] = f"${{{plan.database_secret_ref.name}:?required}}"
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
    for name in ("api", "worker", "migrate"):
        service = services[name]
        service.pop("env_file", None)
        service_environment = dict(environment)
        if managed_database and name in role_database_urls:
            # Only `migrate` (and the `roles` service) keep the owner URL.
            service_environment["KIP_DATABASE_URL"] = role_database_urls[name]
        service.update(_compose_service(plan, service_environment, service_name=name))
        if not managed_database:
            service.get("depends_on", {}).pop("postgres", None)
    if managed_database:
        _add_roles_service(services, project_root=project_root)
        base["x-kip-database-roles"] = MANAGED_ROLES_NOTE
    else:
        services.pop("postgres")
        base["x-kip-database-roles"] = EXTERNAL_ROLES_NOTE.format(
            name=plan.database_secret_ref.display()
        )
    if not plan.semantic_search:
        # Lexical-only plan: no model runtime container (ADR-065).
        services.pop("models", None)
        volumes = base.get("volumes")
        if isinstance(volumes, dict):
            volumes.pop("kip_models", None)
    base["x-kip-setup"] = {"mode": "standalone", "plan_fingerprint": plan.plan_fingerprint}
    # This is a complete Compose project, not an override: merging source
    # mounts would retain the sample NAS mount outside the approved plan.
    return _JSON_OBJECT.validate_python(base)


def _add_roles_service(services: Any, *, project_root: Path) -> None:
    """Copy the one-shot `roles` service into a project that runs its own PostgreSQL.

    Deliberate scope: the service is included only for the bundled database. It
    creates the kip_api / kip_worker logins by connecting to the `postgres`
    service as the image bootstrap owner, and both exist only in that shape.
    When the operator pointed setup at an external database KIP holds neither
    the owner credentials nor the authority to create roles there, so a
    generated project leaves the service out and `x-kip-database-roles` tells
    the operator to apply deploy/sql/roles.sql.template themselves. Emitting it
    anyway would produce a service that fails on every start, or - worse - one
    that silently did nothing while the API kept the operator's owner login.
    """
    try:
        overlay = yaml.safe_load(
            (project_root / "deploy/compose.roles.yaml").read_text(encoding="utf-8")
        )
        roles = overlay["services"]["roles"]
        if not isinstance(roles, dict):
            raise ValueError("roles service is not a mapping")
        if roles.get("volumes") != ["./deploy:/deploy:ro"]:
            raise ValueError("roles service mounts an unexpected path")
        # Long form, like every other mount in a generated project: Compose
        # would otherwise create an empty ./deploy and the apply would fail on a
        # missing template instead of on the missing directory.
        roles["volumes"] = [{
            "type": "bind", "source": "./deploy", "target": "/deploy",
            "read_only": True, "bind": {"create_host_path": False},
        }]
        services["roles"] = roles
        for name in ("api", "worker"):
            # api and worker must not start before the logins they use exist.
            depends_on = services[name].setdefault("depends_on", {})
            depends_on.update(overlay["services"][name]["depends_on"])
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise ValidationError(
            "setup requires the package deploy/compose.roles.yaml database role template"
        ) from exc


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
    *,
    service_name: str,
) -> JsonObject:
    volumes: list[JsonValue] = [
        {
            "type": "bind",
            "source": "./config/kip.generated.toml",
            "target": "/app/config/kip.generated.toml",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]
    volumes.extend(
        {
            "type": "bind",
            "source": mount.source,
            "target": mount.target,
            "read_only": mount.read_only,
            "bind": {"create_host_path": False},
        }
        for mount in plan.mounts
        if service_name != "migrate" or mount.purpose == "cas"
    )
    if service_name != "migrate":
        volumes.append({
            "type": "bind", "source": "${KIP_ONTOLOGY_PATH:-./ontology}",
            "target": "/app/ontology", "read_only": service_name != "api",
            "bind": {"create_host_path": False},
        })
        if plan.model_secret_ref is not None and plan.model_secret_ref.scheme == "file":
            volumes.append({
                "type": "bind", "source": plan.model_secret_ref.name,
                "target": plan.model_secret_ref.name, "read_only": True,
                "bind": {"create_host_path": False},
            })
    runtime_environment = dict(environment)
    if service_name == "api":
        runtime_environment["KIP_API_HOST"] = "0.0.0.0"
    return {
        "environment": {key: value for key, value in runtime_environment.items()},
        "volumes": volumes,
        "user": f"{plan.runtime_uid}:{plan.runtime_gid}",
        "group_add": [str(group) for group in plan.runtime_supplementary_gids or []],
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
