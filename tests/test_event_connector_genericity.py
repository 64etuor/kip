from __future__ import annotations

from pathlib import Path

import pytest

from kip.adapters.connectors.registry import ConfiguredSourceCatalog
from kip.adapters.connectors.slack import SlackConnector
from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.models import ConnectorEvent, RequestContext
from kip.settings import Settings


def _settings(tmp_path: Path, raw: dict) -> Settings:
    return Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw=raw,
        environment="test",
        workspace="acme",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )


def test_capabilities_and_enabled_names_derive_from_remote_source_enumeration(
    tmp_path: Path,
) -> None:
    """Enabled/known remote source surfaces come from iterating the enum,
    not from hand-enumerated per-source literals in the registry."""
    settings = _settings(
        tmp_path,
        {
            "sources": {
                "slack": {"enabled": True, "workspace_id": "W1"},
                "imap": {"enabled": True, "host": "imap.example.com"},
                "apple_mail": {"enabled": False},
            }
        },
    )
    catalog = ConfiguredSourceCatalog(settings)

    assert catalog.capabilities() == {
        "filesystem": "disabled",
        "slack": "configured",
        "apple_mail": "disabled",
        "imap": "configured",
    }
    assert catalog.enabled_names() == ["slack", "imap"]


def test_new_connector_declares_event_family_through_catalog_seam_only(
    tmp_path: Path,
) -> None:
    """A brand-new connector name ("helpdesk-crm") never appears in any
    application-layer source code. Its event formatting (mailbox-style
    locator/URI/unit_type) is driven purely by the `event_family` the
    catalog declares from `sources.connector_policies` configuration,
    proving the fan-out seam is generic: zero application-code edits are
    required to onboard it.
    """
    settings = _settings(
        tmp_path,
        {
            "search": {"semantic_enabled": False},
            "graph": {"backend": "memory"},
            "sources": {
                "connector_policies": [
                    {
                        "name": "helpdesk-crm",
                        "acl_mode": "static",
                        "classification": "internal",
                        "event_family": "mail",
                    }
                ]
            },
        },
    )
    repository = MemoryRepository()
    container = build_container(settings, repository=repository, load_models=False)
    context = RequestContext(
        workspace="acme",
        acl_scopes=["workspace:acme"],
        request_id="req_helpdesk_1",
    )
    event = ConnectorEvent(
        event_id="evt_helpdesk_1",
        connector_name="helpdesk-crm",
        operation="upsert",
        external_id="ticket-482",
        payload={
            "subject": "Ticket #482 업데이트",
            "text": "고객 문의가 접수되었습니다.",
            "account_id": "support@example.com",
            "mailbox": "INBOX",
            "message_id": "msg-482",
            "uid": "482",
        },
        acl_scopes=["workspace:acme"],
    )

    result = container.application.ingestion.ingest_connector_event(context, event)

    assert result.status == "inserted"
    unit = repository.state.units[next(iter(repository.state.units))]
    assert unit.unit_type == "email_message"
    assert unit.locator.type == "email_message"
    assert unit.locator.data["mailbox"] == "INBOX"
    source_object = next(iter(repository.state.artifacts.values())).source_object
    assert source_object is not None
    assert source_object.canonical_uri == "mail://msg-482"


def test_sync_remote_unifies_dispatch_for_an_existing_connector_family(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The single generic `sync_remote` application method (replacing the
    former per-source sync_slack/sync_imap/sync_apple_mail methods) drives a
    real connector end to end, proving the unified dispatch preserves
    behavior for a known remote source.
    """
    monkeypatch.setenv("KIP_SLACK_BOT_TOKEN", "test-token")
    settings = _settings(
        tmp_path,
        {
            "search": {"semantic_enabled": False},
            "graph": {"backend": "memory"},
            "sources": {
                "slack": {
                    "enabled": True,
                    "workspace_id": "W1",
                    "allowed_conversation_ids": ["C1"],
                }
            },
        },
    )
    repository = MemoryRepository()
    container = build_container(settings, repository=repository, load_models=False)
    context = container.application.operations.request_context()

    def fake_call(self: SlackConnector, method: str, payload: dict) -> dict:
        assert method == "conversations.history"
        return {
            "ok": True,
            "messages": [
                {"ts": "100.0", "user": "U_A", "text": "정산 문의드립니다.", "reply_count": 0}
            ],
        }

    monkeypatch.setattr(SlackConnector, "_call", fake_call)

    summary = container.application.ingestion.sync_remote(context, "slack")

    assert summary.source == "slack"
    assert summary.scanned == 1
    assert summary.inserted == 1
    unit = repository.state.units[next(iter(repository.state.units))]
    assert unit.unit_type == "slack_message"
