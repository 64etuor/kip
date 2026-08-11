from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from kip.domain.models import ConnectorEvent
from kip.errors import ConfigurationError, SourceUnavailableError
from kip.ids import sha256_bytes, stable_id


class SlackConnector:
    name = "slack"
    kind = "slack"

    def __init__(self, workspace_id: str, allowed_conversation_ids: list[str], token_env: str = "KIP_SLACK_BOT_TOKEN") -> None:
        token = os.environ.get(token_env, "")
        if not token:
            raise ConfigurationError(f"Slack token environment variable is empty: {token_env}")
        self.workspace_id = workspace_id
        self.allowed_conversation_ids = allowed_conversation_ids
        self.client = httpx.Client(
            base_url="https://slack.com/api/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )

    def pull_messages(self, oldest: str | None = None) -> Iterator[ConnectorEvent]:
        for conversation_id in self.allowed_conversation_ids:
            cursor: str | None = None
            while True:
                payload: dict[str, Any] = {"channel": conversation_id, "limit": 200}
                if oldest:
                    payload["oldest"] = oldest
                if cursor:
                    payload["cursor"] = cursor
                response = self._call("conversations.history", payload)
                for message in response.get("messages", []):
                    thread_ts = message.get("thread_ts")
                    if thread_ts and thread_ts != message.get("ts"):
                        # Broadcast replies surface in history; the thread
                        # event that owns them is emitted from the root.
                        continue
                    if int(message.get("reply_count") or 0) > 0:
                        yield self._thread_event(conversation_id, message)
                    else:
                        yield self._event(conversation_id, message)
                cursor = ((response.get("response_metadata") or {}).get("next_cursor") or "").strip()
                if not cursor:
                    break

    def _collect_thread(
        self,
        conversation_id: str,
        thread_ts: str,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {"channel": conversation_id, "ts": thread_ts, "limit": 200}
            if cursor:
                payload["cursor"] = cursor
            response = self._call("conversations.replies", payload)
            messages.extend(response.get("messages", []))
            cursor = ((response.get("response_metadata") or {}).get("next_cursor") or "").strip()
            if not cursor:
                break
        return messages

    def _thread_event(
        self,
        conversation_id: str,
        root: dict[str, Any],
    ) -> ConnectorEvent:
        """One semantic event per thread: question and answers stay together.

        The external ID stays keyed on the thread root, so new replies become
        a new revision of the same source object instead of scattered units.
        """
        root_ts = str(root["ts"])
        messages = self._collect_thread(conversation_id, root_ts) or [root]
        text = "\n\n".join(
            f"[{message.get('user') or 'unknown'}] {message.get('text', '')}"
            for message in messages
        )
        external_id = f"{self.workspace_id}:{conversation_id}:{root_ts}"
        return ConnectorEvent(
            event_id=stable_id(
                "evt",
                "slack",
                f"{external_id}:thread:{sha256_bytes(text.encode('utf-8'))}",
            ),
            connector_name=self.name,
            operation="upsert",
            external_id=external_id,
            occurred_at=datetime.now(UTC),
            payload={
                "workspace_id": self.workspace_id,
                "conversation_id": conversation_id,
                "ts": root_ts,
                "thread_ts": root_ts,
                "user_id": root.get("user"),
                "text": text,
                "thread": True,
                "message_count": len(messages),
                "raw_thread": messages,
            },
            acl_scopes=[f"slack:{self.workspace_id}:{conversation_id}"],
        )

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        while True:
            response = self.client.get(method, params=payload)
            if response.status_code == 429:
                time.sleep(max(1, int(response.headers.get("Retry-After", "1"))))
                continue
            response.raise_for_status()
            data = cast(dict[str, Any], response.json())
            if not data.get("ok"):
                raise SourceUnavailableError(f"Slack {method} failed: {data.get('error', 'unknown_error')}")
            return data

    def _event(self, conversation_id: str, message: dict[str, Any]) -> ConnectorEvent:
        ts = str(message["ts"])
        subtype = message.get("subtype")
        deleted = subtype == "message_deleted"
        external_id = f"{self.workspace_id}:{conversation_id}:{ts}"
        return ConnectorEvent(
            event_id=stable_id("evt", "slack", external_id + ":" + str(message.get("edited", {}))),
            connector_name=self.name,
            operation="delete" if deleted else "upsert",
            external_id=external_id,
            occurred_at=datetime.now(UTC),
            payload={
                "workspace_id": self.workspace_id,
                "conversation_id": conversation_id,
                "ts": ts,
                "thread_ts": message.get("thread_ts"),
                "user_id": message.get("user"),
                "text": message.get("text", ""),
                "edited": message.get("edited"),
                "files": message.get("files", []),
                "raw": message,
            },
            acl_scopes=[f"slack:{self.workspace_id}:{conversation_id}"],
        )
