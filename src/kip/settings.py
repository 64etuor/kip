from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kip.errors import ConfigurationError


def _deep_get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


@dataclass(slots=True)
class Settings:
    project_root: Path
    config_path: Path
    raw: dict[str, Any] = field(default_factory=dict)
    environment: str = "development"
    workspace: str = "default"
    database_url: str = "memory://"
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

        database_url = os.environ.get("KIP_DATABASE_URL", "")
        if not database_url:
            env_name = _deep_get(raw, "database.url_env", "KIP_DATABASE_URL")
            database_url = os.environ.get(str(env_name), "memory://")

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
            environment=os.environ.get("KIP_ENV", str(_deep_get(raw, "app.environment", "development"))),
            workspace=os.environ.get("KIP_WORKSPACE", str(_deep_get(raw, "app.workspace", "default"))),
            database_url=database_url,
            cas_path=cas_path,
            api_host=os.environ.get("KIP_API_HOST", str(_deep_get(raw, "api.host", "127.0.0.1"))),
            api_port=int(os.environ.get("KIP_API_PORT", _deep_get(raw, "api.port", 8080))),
            api_key=os.environ.get("KIP_API_KEY")
            or os.environ.get(api_key_env, ""),
            admin_key=os.environ.get("KIP_ADMIN_KEY")
            or os.environ.get(admin_key_env, ""),
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
        )

    @classmethod
    def for_test(cls) -> Settings:
        root = Path.cwd().resolve()
        return cls(
            project_root=root,
            config_path=root / "config/kip.example.toml",
            raw={
                "search": {"semantic_enabled": False, "context_max_chars": 40000},
                "graph": {"backend": "memory"},
            },
            environment="test",
            workspace="default",
            database_url="memory://",
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
        if scheme != "env":
            raise ConfigurationError(
                f"secret reference scheme is not available in this runtime: {scheme}"
            )
        value = os.environ.get(name)
        if value is None or not value:
            raise ConfigurationError(f"required secret environment variable is not set: {name}")
        return value

    @property
    def is_memory(self) -> bool:
        return self.database_url.startswith("memory://")
