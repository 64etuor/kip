from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kip.errors import ConfigurationError

_MAX_SECRET_BYTES = 64 * 1024


def _deep_get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _read_secret_file(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        raise ConfigurationError("secret file path must be absolute")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if path.is_symlink():
            raise ConfigurationError("secret file must not be a symlink") from error
        raise ConfigurationError("secret file is not readable") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigurationError("secret file must be a regular file")
        if metadata.st_size > _MAX_SECRET_BYTES:
            raise ConfigurationError("secret file exceeds the size limit")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(_MAX_SECRET_BYTES + 1)
    except OSError as error:
        raise ConfigurationError("secret file is not readable") from error
    finally:
        os.close(descriptor)
    if len(payload) > _MAX_SECRET_BYTES:
        raise ConfigurationError("secret file exceeds the size limit")
    if payload.endswith(b"\n"):
        payload = payload[:-1]
        if payload.endswith(b"\r"):
            payload = payload[:-1]
    try:
        value = payload.decode("utf-8")
    except UnicodeError as error:
        raise ConfigurationError("secret file is not valid UTF-8") from error
    if not value:
        raise ConfigurationError("secret file is empty")
    if "\n" in value or "\r" in value:
        raise ConfigurationError("secret file must contain a single line")
    return value


def _environment_secret(name: str) -> str:
    file_name = f"{name}_FILE"
    if name in os.environ and file_name in os.environ:
        raise ConfigurationError(f"{name} and {file_name} cannot both be set")
    if file_name in os.environ:
        return _read_secret_file(os.environ[file_name])
    return os.environ.get(name, "")


def _positive_integer(value: object, name: str) -> int:
    try:
        parsed = int(str(value))
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a positive integer") from error
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return parsed


# Configuration keys this build understands. A key that is present in a
# loaded TOML file but absent here is reported once as a startup warning, so
# a misspelling (`serach.default_mode`) or a key left behind by an older
# release stops being silently ignored.
#
# Why an explicit list rather than reflection over the code: the readers are
# spread across the container, adapters and use cases, many of them reading a
# whole sub-table and then `.get()`-ing inside it, so no runtime reflection
# can enumerate them. Deriving the set from `config/kip.example.toml` at
# runtime is not an option either — a deployment ships its own config and
# need not carry the example. The list is therefore explicit and guarded
# against rot by `tests/test_config_key_validation.py`, which fails whenever
# the shipped example config or the setup writer's payload contains a key
# this list does not recognise.
#
# `*` matches exactly one path segment (dynamically named tables, such as the
# per-parser tables under `[parsers.hwp]`); `[]` marks an array of tables.
_RECOGNISED_CONFIG_KEYS: tuple[str, ...] = (
    "app.environment",
    "app.workspace",
    "app.log_level",
    "setup.plan_fingerprint",
    "setup.source_ownership",
    "database.url_env",
    "database.secret_ref",
    "database.statement_timeout_ms",
    "database.projection_statement_timeout_ms",
    "database.pool_max_size",
    "storage.cas_path",
    "api.host",
    "api.port",
    "api.max_request_bytes",
    "identity.mode",
    "identity.owner",
    "identity.api_key.principal_id",
    "identity.api_key.acl_scopes",
    "identity.api_key.api_key_env",
    "identity.api_key.admin_key_env",
    "identity.api_key.secret_ref",
    "identity.api_key.admin_secret_ref",
    "identity.jwt.issuer",
    "identity.jwt.audience",
    "identity.jwt.jwks_url",
    "identity.jwt.algorithms",
    "identity.jwt.principal_claim",
    "identity.jwt.workspace_claim",
    "identity.jwt.group_claim",
    "identity.jwt.scope_claim",
    "identity.jwt.group_scope_prefix",
    "identity.jwt.admin_groups",
    "identity.jwt.snapshot_id_claim",
    "identity.jwt.snapshot_version_claim",
    "identity.jwt.snapshot_captured_at_claim",
    "identity.jwt.snapshot_expires_at_claim",
    "identity.jwt.jwks_cache_seconds",
    "identity.jwt.jwks_timeout_seconds",
    "identity.jwt.clock_skew_seconds",
    "security.allow_remote_model_egress",
    "security.model_service_hosts",
    "security.follow_symlinks",
    "security.max_file_bytes",
    "telemetry.query_traces_enabled",
    "telemetry.retention_days",
    "search.semantic_enabled",
    "search.default_mode",
    "search.semantic_auto_activate",
    "search.context_item_max_chars",
    "search.alias_expansion_enabled",
    "search.alias_expansion_max_terms",
    "search.max_hits_per_document",
    "search.abstain_on_unknown_terms",
    "search.korean_ngram_min",
    "search.korean_ngram_max",
    "search.hybrid_candidate_limit",
    "search.rerank_candidate_limit",
    "search.lexical_rerank_enabled",
    "search.lexical_rerank_candidate_limit",
    "search.rrf_rank_constant",
    "search.hnsw_ef_search",
    "search.hnsw_max_scan_tuples",
    "search.lexical_common_term_fraction",
    "models.circuit_cooldown_seconds",
    "models.embedding.enabled",
    "models.embedding.base_url",
    "models.embedding.model",
    "models.embedding.revision",
    "models.embedding.dimensions",
    "models.embedding.batch_size",
    "models.embedding.page_size",
    "models.embedding.max_batch_chars",
    "models.embedding.max_document_chars",
    "models.embedding.timeout_seconds",
    "models.embedding.query_timeout_seconds",
    "models.embedding.query_instruction",
    "models.embedding.space_name",
    "models.embedding.document_instruction",
    # `[models.reranker]` and the optional `[models.lexical_reranker]` are
    # read by the same adapter factory and accept the same keys.
    "models.reranker.*",
    "models.lexical_reranker.*",
    "models.generation.enabled",
    "models.generation.provider",
    "models.generation.base_url",
    "models.generation.model",
    "models.generation.revision",
    "models.generation.allowed_classifications",
    "models.generation.retention_policy",
    "models.generation.secret_ref",
    "models.generation.api_key_env",
    "models.generation.timeout_seconds",
    "models.generation.max_response_bytes",
    "models.generation.max_claims",
    "models.generation.max_output_tokens",
    "models.generation.fallback_on_error",
    "models.relation_mining.enabled",
    "models.relation_mining.max_units",
    "models.relation_mining.max_characters",
    "models.relation_mining.max_entity_proposals",
    "models.relation_mining.max_relation_proposals",
    "ontology.domain_profile",
    "ontology.adaptive_discovery",
    "ontology.reviewers",
    "ontology.auto_approve.enabled",
    "ontology.auto_approve.min_precision",
    "ontology.auto_approve.min_confidence",
    "ontology.auto_approve.min_reviewed",
    "ontology.answer_context.entity_limit",
    "ontology.answer_context.edge_limit",
    "ontology.answer_context.max_depth",
    "ontology.migrations.max_assertions",
    "interaction.enabled",
    "interaction.clarification_ttl_seconds",
    "sync.deletion_grace_scans",
    "parsers.parser_timeout_seconds",
    "parsers.minimum_quality_score",
    "parsers.isolation.enabled",
    "parsers.isolation.wall_seconds",
    "parsers.isolation.cpu_seconds",
    "parsers.isolation.memory_mib",
    "parsers.isolation.result_mib",
    "parsers.isolation.diagnostic_kib",
    "parsers.isolation.cpu_threads",
    "parsers.isolation.nice",
    "parsers.pdf.backend",
    "parsers.pdf.tables_enabled",
    "parsers.ocr.timeout_seconds",
    "parsers.ocr.kordoc.enabled",
    "parsers.ocr.kordoc.argv",
    "parsers.ocr.kordoc.version_argv",
    "parsers.ocr.kordoc.expected_version",
    "parsers.ocr.pptx.max_images",
    "parsers.ocr.pptx.max_image_bytes",
    "parsers.ocr.pptx.max_total_bytes",
    "parsers.ocr.pptx.min_width_px",
    "parsers.ocr.pptx.min_height_px",
    "parsers.hwp.order",
    # Per-parser tables are named by `parsers.hwp.order`.
    "parsers.hwp.*.enabled",
    "parsers.hwp.*.argv",
    "parsers.hwp.*.max_chars_per_unit",
    "sources.filesystem[].name",
    "sources.filesystem[].root",
    "sources.filesystem[].enabled",
    "sources.filesystem[].read_only",
    "sources.filesystem[].follow_symlinks",
    "sources.filesystem[].settle_seconds",
    "sources.filesystem[].include_extensions",
    "sources.filesystem[].exclude_globs",
    "sources.filesystem[].acl_scope",
    "sources.filesystem[].classification",
    "sources.slack.enabled",
    "sources.slack.workspace_id",
    "sources.slack.allowed_conversation_ids",
    "sources.slack.acl_snapshot_ttl_seconds",
    "sources.slack.token_env",
    "sources.slack.classification",
    "sources.apple_mail.enabled",
    "sources.apple_mail.allowed_accounts",
    "sources.apple_mail.allowed_mailboxes",
    "sources.apple_mail.lookback_days",
    "sources.apple_mail.limit_per_mailbox",
    "sources.apple_mail.acl_snapshot_ttl_seconds",
    "sources.apple_mail.classification",
    "sources.imap.enabled",
    "sources.imap.host",
    "sources.imap.port",
    "sources.imap.username_env",
    "sources.imap.password_env",
    "sources.imap.mailboxes",
    "sources.imap.use_ssl",
    "sources.imap.acl_snapshot_ttl_seconds",
    "sources.imap.classification",
    "sources.connector_policies[].name",
    "sources.connector_policies[].event_family",
    "sources.connector_policies[].classification",
    "sources.connector_policies[].acl_mode",
    "sources.connector_policies[].acl_scopes",
    "sources.connector_policies[].acl_snapshot_ttl_seconds",
    "operations.backup_path",
    "operations.retention_days",
    "operations.sync_schedule",
    "evaluation.dataset",
)


def _config_key_paths(value: Any, prefix: str = "") -> list[str]:
    """Every leaf key path in a loaded config, arrays of tables included."""
    if isinstance(value, dict):
        return [
            path
            for key, item in value.items()
            for path in _config_key_paths(item, f"{prefix}.{key}" if prefix else str(key))
        ]
    if isinstance(value, list) and any(isinstance(item, dict) for item in value):
        return [
            path
            for item in value
            for path in _config_key_paths(item, f"{prefix}[]")
        ]
    return [prefix] if prefix else []


def _is_recognised_config_key(path: str) -> bool:
    segments = path.split(".")
    for pattern in _RECOGNISED_CONFIG_KEYS:
        parts = pattern.split(".")
        if len(parts) == len(segments) and all(
            part in {"*", segment} for part, segment in zip(parts, segments, strict=True)
        ):
            return True
        # An empty array of tables, such as `sources.filesystem = []`, yields
        # the table-array prefix itself; the build reads it, so it is known.
        if len(parts) > len(segments) and parts[len(segments) - 1] == f"{segments[-1]}[]" and all(
            part in {"*", segment}
            for part, segment in zip(parts[: len(segments) - 1], segments[:-1], strict=True)
        ):
            return True
    return False


def unknown_config_keys(raw: dict[str, Any]) -> tuple[str, ...]:
    """Keys present in a loaded config that no part of this build reads."""
    return tuple(
        dict.fromkeys(
            path for path in _config_key_paths(raw) if not _is_recognised_config_key(path)
        )
    )


@dataclass(slots=True)
class Settings:
    project_root: Path
    config_path: Path
    raw: dict[str, Any] = field(default_factory=dict)
    environment: str = "development"
    workspace: str = "default"
    database_url: str = "memory://"
    database_statement_timeout_ms: int = 15000
    database_pool_max_size: int = 10
    cas_path: Path = Path("./var/cas")
    api_host: str = "127.0.0.1"
    api_port: int = 8080
    api_key: str = ""
    admin_key: str = ""
    identity_mode: str = "api_key"
    identity_api_key_principal_id: str = "principal_api"
    identity_api_key_acl_scopes: tuple[str, ...] = ()
    jwt_issuer: str = ""
    jwt_audience: str = ""
    jwt_jwks_url: str = ""
    max_request_bytes: int = 10 * 1024 * 1024
    log_level: str = "INFO"
    # Config keys present in the file but unknown to this build. Reported as
    # a capability warning; never a startup failure, so an operator is never
    # locked out by a key a newer or older release wrote.
    unknown_config_keys: tuple[str, ...] = ()

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> Settings:
        configured_root = os.environ.get("KIP_PROJECT_ROOT")
        root = Path(configured_root) if configured_root else Path.cwd()
        root = root.resolve()
        configured_path = config_path or os.environ.get("KIP_CONFIG")
        path = Path(configured_path) if configured_path else root / "config/kip.toml"
        if not path.is_absolute():
            path = (root / path).resolve()
        raw: dict[str, Any] = {}
        if path.exists():
            with path.open("rb") as handle:
                raw = tomllib.load(handle)
        elif os.environ.get("KIP_ENV", "development") not in {"test", "development"}:
            raise ConfigurationError(f"configuration file does not exist: {path}")

        env_name = str(_deep_get(raw, "database.url_env", "KIP_DATABASE_URL"))
        database_url = _environment_secret(env_name)
        environment = os.environ.get(
            "KIP_ENV", str(_deep_get(raw, "app.environment", "development")),
        )
        if not database_url:
            # The memory repository is non-durable: every ingested unit, job
            # and assertion disappears with the process. Booting it because a
            # database URL happened to be unset is a silent data-loss default,
            # so only the test environment may fall back; any other
            # environment fails loudly and names the variable to set.
            if env_name != "KIP_DATABASE_URL" or environment != "test":
                hint = (
                    "set it to a PostgreSQL URL"
                    if env_name != "KIP_DATABASE_URL"
                    else "set it to a PostgreSQL URL, or run with KIP_ENV=test to use "
                    "the non-durable memory repository"
                )
                raise ConfigurationError(
                    f"required database secret is not set: {env_name} "
                    f"(or {env_name}_FILE); {hint}"
                )
            database_url = "memory://"

        cas_value = os.environ.get("KIP_CAS_PATH", _deep_get(raw, "storage.cas_path", "./var/cas"))
        cas_path = Path(str(cas_value))
        if not cas_path.is_absolute():
            cas_path = (root / cas_path).resolve()

        api_key_env = str(
            _deep_get(raw, "identity.api_key.api_key_env", "KIP_API_KEY")
        )
        admin_key_env = str(
            _deep_get(raw, "identity.api_key.admin_key_env", "KIP_ADMIN_KEY")
        )

        return cls(
            project_root=root,
            config_path=path,
            raw=raw,
            environment=environment,
            workspace=os.environ.get("KIP_WORKSPACE", str(_deep_get(raw, "app.workspace", "default"))),
            database_url=database_url,
            database_statement_timeout_ms=_positive_integer(
                os.environ.get(
                    "KIP_DATABASE_STATEMENT_TIMEOUT_MS",
                    _deep_get(raw, "database.statement_timeout_ms", 15000),
                ),
                "KIP_DATABASE_STATEMENT_TIMEOUT_MS",
            ),
            database_pool_max_size=_positive_integer(
                os.environ.get(
                    "KIP_DATABASE_POOL_MAX_SIZE",
                    _deep_get(raw, "database.pool_max_size", 10),
                ),
                "KIP_DATABASE_POOL_MAX_SIZE",
            ),
            cas_path=cas_path,
            api_host=os.environ.get("KIP_API_HOST", str(_deep_get(raw, "api.host", "127.0.0.1"))),
            api_port=int(os.environ.get("KIP_API_PORT", _deep_get(raw, "api.port", 8080))),
            api_key=_environment_secret(api_key_env),
            admin_key=_environment_secret(admin_key_env),
            identity_mode=os.environ.get(
                "KIP_IDENTITY_MODE",
                str(_deep_get(raw, "identity.mode", "api_key")),
            ),
            identity_api_key_principal_id=os.environ.get(
                "KIP_API_PRINCIPAL_ID",
                str(
                    _deep_get(
                        raw,
                        "identity.api_key.principal_id",
                        "principal_api",
                    )
                ),
            ),
            identity_api_key_acl_scopes=tuple(
                item.strip()
                for item in os.environ.get(
                    "KIP_API_ACL_SCOPES",
                    ",".join(
                        str(item)
                        for item in (
                            _deep_get(raw, "identity.api_key.acl_scopes", []) or []
                        )
                    ),
                ).split(",")
                if item.strip()
            ),
            jwt_issuer=os.environ.get(
                "KIP_JWT_ISSUER",
                str(_deep_get(raw, "identity.jwt.issuer", "")),
            ),
            jwt_audience=os.environ.get(
                "KIP_JWT_AUDIENCE",
                str(_deep_get(raw, "identity.jwt.audience", "")),
            ),
            jwt_jwks_url=os.environ.get(
                "KIP_JWT_JWKS_URL",
                str(_deep_get(raw, "identity.jwt.jwks_url", "")),
            ),
            max_request_bytes=int(
                os.environ.get("KIP_MAX_REQUEST_BYTES", _deep_get(raw, "api.max_request_bytes", 10 * 1024 * 1024))
            ),
            log_level=os.environ.get("KIP_LOG_LEVEL", str(_deep_get(raw, "app.log_level", "INFO"))),
            unknown_config_keys=unknown_config_keys(raw),
        )

    @classmethod
    def for_test(cls) -> Settings:
        root = Path.cwd().resolve()
        return cls(
            project_root=root,
            config_path=root / "config/kip.example.toml",
            raw={
                "search": {"semantic_enabled": False},
            },
            environment="test",
            workspace="default",
            database_url="memory://",
            database_statement_timeout_ms=15000,
            cas_path=root / "var/test-cas",
            api_key="test-key",
            admin_key="test-admin-key",
            identity_mode="api_key",
            identity_api_key_principal_id="principal_api",
            identity_api_key_acl_scopes=("workspace:default",),
        )

    def get(self, path: str, default: Any = None) -> Any:
        return _deep_get(self.raw, path, default)

    def filesystem_source(self, name: str) -> dict[str, Any] | None:
        sources = _deep_get(self.raw, "sources.filesystem", [])
        if not isinstance(sources, list):
            return None
        for source in sources:
            if isinstance(source, dict) and source.get("name") == name:
                return source
        return None

    def resolve_secret_reference(self, reference: str) -> str:
        scheme, separator, name = reference.partition(":")
        if not separator or not name:
            raise ConfigurationError("secret must be an opaque reference")
        if scheme == "file":
            return _read_secret_file(name)
        if scheme != "env":
            raise ConfigurationError(
                f"secret reference scheme is not available in this runtime: {scheme}"
            )
        value = _environment_secret(name)
        if not value:
            raise ConfigurationError(f"required secret environment variable is not set: {name}")
        return value

    @property
    def is_memory(self) -> bool:
        return self.database_url.startswith("memory://")
