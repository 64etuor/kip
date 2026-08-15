from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum, unique
from pathlib import Path
from typing import assert_never

from pydantic import BaseModel, ConfigDict, Field

from kip.adapters.connectors.apple_mail import AppleMailConnector
from kip.adapters.connectors.filesystem import FileSystemConnector
from kip.adapters.connectors.imap import ImapConnector
from kip.adapters.connectors.slack import SlackConnector
from kip.domain.egress import DataClassification
from kip.domain.identity import AclSnapshot
from kip.domain.models import ConnectorEvent
from kip.errors import ConfigurationError
from kip.ids import new_id, sha256_bytes, stable_id
from kip.ports.ingestion import DiscoveredFile, FilesystemSourcePort
from kip.settings import Settings


@unique
class RemoteSourceName(StrEnum):
    SLACK = "slack"
    APPLE_MAIL = "apple-mail"
    IMAP = "imap"


def _remote_event_family(source_name: RemoteSourceName) -> str:
    match source_name:
        case RemoteSourceName.SLACK:
            return "slack"
        case RemoteSourceName.APPLE_MAIL | RemoteSourceName.IMAP:
            return "mail"
        case unreachable:
            assert_never(unreachable)


class SlackSourceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    enabled: bool = False
    workspace_id: str = ""
    allowed_conversation_ids: list[str] = Field(default_factory=list)
    token_env: str = "KIP_SLACK_BOT_TOKEN"
    classification: DataClassification = DataClassification.RESTRICTED


class AppleMailSourceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    enabled: bool = False
    allowed_accounts: list[str] = Field(default_factory=list)
    allowed_mailboxes: list[str] = Field(default_factory=list)
    lookback_days: int = 30
    limit_per_mailbox: int = 500
    classification: DataClassification = DataClassification.RESTRICTED


class ImapSourceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    enabled: bool = False
    host: str = ""
    port: int = 993
    mailboxes: list[str] = Field(default_factory=list)
    username_env: str = "KIP_IMAP_USERNAME"
    password_env: str = "KIP_IMAP_PASSWORD"
    use_ssl: bool = True
    classification: DataClassification = DataClassification.RESTRICTED


@dataclass(frozen=True, slots=True)
class ConfiguredFilesystemSource:
    name: str
    root: Path
    acl_scope: str | None
    acl_snapshot: AclSnapshot
    classification: DataClassification
    connector: FileSystemConnector

    def scan(
        self,
        *,
        include_extensions: set[str] | None = None,
    ) -> Iterable[DiscoveredFile]:
        return self.connector.scan(include_extensions=include_extensions)


@dataclass(frozen=True, slots=True)
class ConfiguredSourceCatalog:
    settings: Settings

    def capabilities(self) -> dict[str, str]:
        capabilities = {
            "filesystem": "configured"
            if self.settings.get("sources.filesystem", [])
            else "disabled",
        }
        for name in RemoteSourceName:
            capabilities[_remote_config_key(name.value)] = (
                "configured" if self._remote_source_enabled(name) else "disabled"
            )
        return capabilities

    def enabled_names(self) -> list[str]:
        names = [
            str(source["name"])
            for source in self.settings.get("sources.filesystem", []) or []
            if isinstance(source, dict)
            and source.get("enabled", True)
            and source.get("name")
        ]
        names.extend(
            name.value for name in RemoteSourceName if self._remote_source_enabled(name)
        )
        return names

    def _remote_source_enabled(self, name: RemoteSourceName) -> bool:
        return bool(
            self.settings.get(f"sources.{_remote_config_key(name.value)}.enabled", False)
        )

    def filesystem(self, source_name: str) -> FilesystemSourcePort:
        source = self.settings.filesystem_source(source_name)
        if not source or not source.get("enabled", True):
            raise ConfigurationError(
                f"filesystem source is missing or disabled: {source_name}"
            )
        configured_root = Path(str(source.get("root", "")))
        root = (
            configured_root
            if configured_root.is_absolute()
            else self.settings.project_root / configured_root
        )
        connector = FileSystemConnector(
            root,
            include_extensions={
                str(item).lower() for item in source.get("include_extensions", [])
            },
            exclude_globs=[str(item) for item in source.get("exclude_globs", [])],
            settle_seconds=float(source.get("settle_seconds", 2)),
            follow_symlinks=bool(
                self.settings.get("security.follow_symlinks", False)
            ),
            max_file_bytes=int(
                self.settings.get("security.max_file_bytes", 500 * 1024 * 1024)
            ),
        )
        scope = str(source["acl_scope"]) if source.get("acl_scope") else f"workspace:{self.settings.workspace}"
        classification = _classification(
            source.get("classification", DataClassification.RESTRICTED),
            source_name,
        )
        policy_bytes = json.dumps(
            {
                "name": source_name,
                "root": str(root.resolve()),
                "acl_scope": scope,
                "classification": classification,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        policy_version = sha256_bytes(policy_bytes)
        return ConfiguredFilesystemSource(
            name=source_name,
            root=root,
            acl_scope=scope,
            acl_snapshot=AclSnapshot.configuration(
                snapshot_id=stable_id(
                    "aclsnap",
                    self.settings.workspace,
                    f"filesystem:{source_name}:{policy_version}",
                ),
                version=policy_version,
                provider=f"filesystem:{source_name}",
                scopes=[scope],
            ),
            classification=classification,
            connector=connector,
        )

    def event_classification(self, event: ConnectorEvent) -> DataClassification:
        if event.connector_name in {item.value for item in RemoteSourceName}:
            config_key = _remote_config_key(event.connector_name)
            return _classification(
                self.settings.get(
                    f"sources.{config_key}.classification",
                    DataClassification.RESTRICTED,
                ),
                event.connector_name,
            )
        policy = self._connector_policy(event.connector_name)
        if policy is None:
            if self.settings.environment not in {"development", "test"}:
                raise ConfigurationError(
                    f"connector classification is not configured: {event.connector_name}"
                )
            return DataClassification.RESTRICTED
        try:
            return _classification(policy["classification"], event.connector_name)
        except (KeyError, ValueError) as exc:
            raise ConfigurationError(
                f"connector classification is invalid: {event.connector_name}"
            ) from exc

    def event_family(self, source_name: str) -> str:
        if source_name in {item.value for item in RemoteSourceName}:
            return _remote_event_family(RemoteSourceName(source_name))
        policy = self._connector_policy(source_name)
        if policy is not None and policy.get("event_family"):
            return str(policy["event_family"])
        return "connector"

    def _connector_policy(self, connector_name: str) -> dict[str, object] | None:
        policies = self.settings.get("sources.connector_policies", []) or []
        return next(
            (
                item
                for item in policies
                if isinstance(item, dict) and item.get("name") == connector_name
            ),
            None,
        )

    def event_acl_snapshot(self, event: ConnectorEvent) -> AclSnapshot:
        if event.acl_snapshot is not None:
            return event.acl_snapshot
        configured = self.settings.get(
            f"sources.{_remote_config_key(event.connector_name)}.acl_snapshot_ttl_seconds",
            None,
        )
        if event.connector_name in {item.value for item in RemoteSourceName}:
            ttl_seconds = int(configured or 900)
            captured_at = datetime.now(UTC)
            return AclSnapshot(
                id=new_id("aclsnap"),
                version=event.event_id,
                provider=event.connector_name,
                scopes=list(event.acl_scopes),
                captured_at=captured_at,
                expires_at=captured_at + timedelta(seconds=ttl_seconds),
            )
        policy = self._connector_policy(event.connector_name)
        if policy is None:
            if self.settings.environment not in {"development", "test"}:
                raise ConfigurationError(
                    f"connector ACL policy is not configured: {event.connector_name}"
                )
            return AclSnapshot.configuration(
                snapshot_id=stable_id(
                    "aclsnap",
                    self.settings.workspace,
                    f"development:{event.connector_name}",
                ),
                version="development-configuration-v1",
                provider=f"connector:{event.connector_name}",
                scopes=list(event.acl_scopes),
            )
        mode = str(policy.get("acl_mode", "dynamic"))
        if mode == "static":
            version = sha256_bytes(
                json.dumps(policy, sort_keys=True, default=str).encode("utf-8")
            )
            return AclSnapshot.configuration(
                snapshot_id=stable_id(
                    "aclsnap",
                    self.settings.workspace,
                    f"connector:{event.connector_name}:{version}",
                ),
                version=version,
                provider=f"connector:{event.connector_name}",
                scopes=list(event.acl_scopes),
            )
        if mode != "dynamic":
            raise ConfigurationError(
                f"unsupported connector ACL mode for {event.connector_name}: {mode}"
            )
        ttl_seconds = int(str(policy.get("acl_snapshot_ttl_seconds", 900)))
        if ttl_seconds <= 0:
            raise ConfigurationError("connector ACL snapshot TTL must be positive")
        captured_at = datetime.now(UTC)
        return AclSnapshot(
            id=new_id("aclsnap"),
            version=event.event_id,
            provider=f"connector:{event.connector_name}",
            scopes=list(event.acl_scopes),
            captured_at=captured_at,
            expires_at=captured_at + timedelta(seconds=ttl_seconds),
        )

    def events(
        self,
        source_name: str,
        *,
        since: str | None = None,
    ) -> Iterable[ConnectorEvent]:
        try:
            selected = RemoteSourceName(source_name)
        except ValueError as exc:
            raise ConfigurationError(f"unsupported event source: {source_name}") from exc
        match selected:
            case RemoteSourceName.SLACK:
                slack_config = SlackSourceConfig.model_validate(
                    self.settings.get("sources.slack", {}) or {}
                )
                if not slack_config.enabled:
                    raise ConfigurationError("slack connector is disabled")
                slack_connector = SlackConnector(
                    workspace_id=slack_config.workspace_id,
                    allowed_conversation_ids=slack_config.allowed_conversation_ids,
                    token_env=slack_config.token_env,
                )
                return slack_connector.pull_messages(oldest=since)
            case RemoteSourceName.APPLE_MAIL:
                apple_mail_config = AppleMailSourceConfig.model_validate(
                    self.settings.get("sources.apple_mail", {}) or {}
                )
                if not apple_mail_config.enabled:
                    raise ConfigurationError("apple_mail connector is disabled")
                apple_mail_connector = AppleMailConnector(
                    script_path=self.settings.project_root
                    / "scripts/apple_mail_export.jxa",
                    allowed_accounts=apple_mail_config.allowed_accounts,
                    allowed_mailboxes=apple_mail_config.allowed_mailboxes,
                    lookback_days=apple_mail_config.lookback_days,
                    limit_per_mailbox=apple_mail_config.limit_per_mailbox,
                )
                return apple_mail_connector.pull()
            case RemoteSourceName.IMAP:
                imap_config = ImapSourceConfig.model_validate(
                    self.settings.get("sources.imap", {}) or {}
                )
                if not imap_config.enabled:
                    raise ConfigurationError("imap connector is disabled")
                imap_connector = ImapConnector(
                    host=imap_config.host,
                    port=imap_config.port,
                    mailboxes=imap_config.mailboxes,
                    username_env=imap_config.username_env,
                    password_env=imap_config.password_env,
                    use_ssl=imap_config.use_ssl,
                )
                return imap_connector.pull()
            case unreachable:
                assert_never(unreachable)


def _remote_config_key(connector_name: str) -> str:
    return "apple_mail" if connector_name == RemoteSourceName.APPLE_MAIL else connector_name


def _classification(value: object, source_name: str) -> DataClassification:
    try:
        return DataClassification(str(value))
    except ValueError as exc:
        raise ConfigurationError(
            f"source classification is invalid: {source_name}"
        ) from exc
