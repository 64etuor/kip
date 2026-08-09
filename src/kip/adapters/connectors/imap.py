from __future__ import annotations

import email
import imaplib
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage

from kip.domain.models import ConnectorEvent
from kip.errors import ConfigurationError, SourceUnavailableError
from kip.ids import sha256_bytes, stable_id


class ImapConnector:
    name = "imap"
    kind = "mail"

    def __init__(self, host: str, port: int, mailboxes: list[str], username_env: str, password_env: str, use_ssl: bool = True) -> None:
        self.host = host
        self.port = port
        self.mailboxes = mailboxes
        self.username = os.environ.get(username_env, "")
        self.password = os.environ.get(password_env, "")
        self.use_ssl = use_ssl
        if not self.username or not self.password:
            raise ConfigurationError("IMAP username/password environment variables are empty")

    def pull(self, cursors: dict[str, int] | None = None) -> Iterator[ConnectorEvent]:
        cursors = cursors or {}
        client_cls = imaplib.IMAP4_SSL if self.use_ssl else imaplib.IMAP4
        with client_cls(self.host, self.port) as client:
            client.login(self.username, self.password)
            for mailbox in self.mailboxes:
                status, _ = client.select(f'"{mailbox}"', readonly=True)
                if status != "OK":
                    continue
                start_uid = int(cursors.get(mailbox, 0)) + 1
                status, data = client.uid("search", f"UID {start_uid}:*")
                if status != "OK" or not data:
                    continue
                for uid in data[0].split():
                    status, parts = client.uid("fetch", uid, "(RFC822 UIDVALIDITY)")
                    if status != "OK" or not parts:
                        continue
                    raw = next((part[1] for part in parts if isinstance(part, tuple)), None)
                    if not isinstance(raw, bytes):
                        continue
                    yield self._event(mailbox, uid.decode(), raw)

    def _event(self, mailbox: str, uid: str, raw: bytes) -> ConnectorEvent:
        message = email.message_from_bytes(raw, policy=policy.default)
        message_id = str(message.get("Message-ID") or "").strip()
        identity = message_id or f"{mailbox}:{uid}:{sha256_bytes(raw)}"
        return ConnectorEvent(
            event_id=stable_id("evt", "imap", f"{self.username}:{mailbox}:{uid}:{sha256_bytes(raw)}"),
            connector_name=self.name,
            operation="upsert",
            external_id=identity,
            occurred_at=datetime.now(UTC),
            payload={
                "account_id": self.username,
                "mailbox": mailbox,
                "uid": uid,
                "message_id": message_id,
                "in_reply_to": str(message.get("In-Reply-To") or ""),
                "references": str(message.get("References") or ""),
                "subject": str(message.get("Subject") or ""),
                "from": str(message.get("From") or ""),
                "to": str(message.get("To") or ""),
                "cc": str(message.get("Cc") or ""),
                "date": str(message.get("Date") or ""),
                "text": self._body(message),
                "rfc822_sha256": sha256_bytes(raw),
                "raw_rfc822": raw.decode("utf-8", errors="replace"),
            },
            acl_scopes=[f"mail:{self.username}:{mailbox}"],
        )

    @staticmethod
    def _body(message: EmailMessage) -> str:
        if message.is_multipart():
            parts: list[str] = []
            for part in message.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                if part.get_content_type() == "text/plain":
                    try:
                        parts.append(part.get_content())
                    except Exception:
                        payload = part.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            parts.append(
                                payload.decode(
                                    part.get_content_charset() or "utf-8",
                                    errors="replace",
                                )
                            )
                        elif isinstance(payload, str):
                            parts.append(payload)
            return "\n".join(parts)
        try:
            return str(message.get_content())
        except Exception as exc:
            raise SourceUnavailableError(f"unable to decode email body: {exc}") from exc
