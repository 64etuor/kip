from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import assert_never

from pydantic import BaseModel, ConfigDict, Field

from kip.adapters.connectors.apple_mail import AppleMailConnector
from kip.adapters.connectors.filesystem import FileSystemConnector
from kip.adapters.connectors.imap import ImapConnector
from kip.adapters.connectors.slack import SlackConnector
from kip.domain.models import ConnectorEvent
from kip.errors import ConfigurationError
from kip.ports.ingestion import DiscoveredFile, FilesystemSourcePort
from kip.settings import Settings


@unique
class RemoteSourceName(StrEnum):
    SLACK = "slack"
    APPLE_MAIL = "apple-mail"
    IMAP = "imap"


class SlackSourceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    enabled: bool = False
    workspace_id: str = ""
    allowed_conversation_ids: list[str] = Field(default_factory=list)
    token_env: str = "KIP_SLACK_BOT_TOKEN"


class AppleMailSourceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    enabled: bool = False
    allowed_accounts: list[str] = Field(default_factory=list)
    allowed_mailboxes: list[str] = Field(default_factory=list)
    lookback_days: int = 30
    limit_per_mailbox: int = 500


class ImapSourceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    enabled: bool = False
    host: str = ""
    port: int = 993
    mailboxes: list[str] = Field(default_factory=list)
    username_env: str = "KIP_IMAP_USERNAME"
    password_env: str = "KIP_IMAP_PASSWORD"
    use_ssl: bool = True


@dataclass(frozen=True, slots=True)
class ConfiguredFilesystemSource:
    name: str
    root: Path
    acl_scope: str | None
    connector: FileSystemConnector

    def scan(self) -> Iterable[DiscoveredFile]:
        return self.connector.scan()


@dataclass(frozen=True, slots=True)
class ConfiguredSourceCatalog:
    settings: Settings

    def capabilities(self) -> dict[str, str]:
        return {
            "filesystem": "configured"
            if self.settings.get("sources.filesystem", [])
            else "disabled",
            "slack": "configured"
            if self.settings.get("sources.slack.enabled", False)
            else "disabled",
            "apple_mail": "configured"
            if self.settings.get("sources.apple_mail.enabled", False)
            else "disabled",
            "imap": "configured"
            if self.settings.get("sources.imap.enabled", False)
            else "disabled",
        }

    def enabled_names(self) -> list[str]:
        names = [
            str(source["name"])
            for source in self.settings.get("sources.filesystem", []) or []
            if isinstance(source, dict)
            and source.get("enabled", True)
            and source.get("name")
        ]
        remote = (
            (RemoteSourceName.SLACK, "sources.slack.enabled"),
            (RemoteSourceName.APPLE_MAIL, "sources.apple_mail.enabled"),
            (RemoteSourceName.IMAP, "sources.imap.enabled"),
        )
        names.extend(name.value for name, key in remote if self.settings.get(key, False))
        return names

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
        return ConfiguredFilesystemSource(
            name=source_name,
            root=root,
            acl_scope=str(source["acl_scope"]) if source.get("acl_scope") else None,
            connector=connector,
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
