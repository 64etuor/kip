from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kip.domain.models import ConnectorEvent
from kip.errors import DependencyUnavailableError, SourceUnavailableError
from kip.ids import stable_id


class AppleMailConnector:
    name = "apple-mail"
    kind = "mail"

    def __init__(self, script_path: Path, allowed_accounts: list[str], allowed_mailboxes: list[str], lookback_days: int = 30, limit_per_mailbox: int = 500) -> None:
        self.script_path = script_path
        self.allowed_accounts = allowed_accounts
        self.allowed_mailboxes = allowed_mailboxes
        self.lookback_days = lookback_days
        self.limit_per_mailbox = limit_per_mailbox

    def pull(self) -> Iterator[ConnectorEvent]:
        if shutil.which("osascript") is None:
            raise DependencyUnavailableError("Apple Mail connector requires macOS osascript")
        since = (datetime.now(UTC) - timedelta(days=self.lookback_days)).isoformat()
        completed = subprocess.run(
            [
                "osascript",
                "-l",
                "JavaScript",
                str(self.script_path),
                json.dumps(self.allowed_accounts),
                json.dumps(self.allowed_mailboxes),
                since,
                str(self.limit_per_mailbox),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if completed.returncode != 0:
            raise SourceUnavailableError(completed.stderr[-2000:])
        for line in completed.stdout.splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            identity = record.get("message_id") or f"{record['account']}:{record['mailbox']}:{record['mail_internal_id']}"
            yield ConnectorEvent(
                event_id=stable_id("evt", "apple-mail", identity + ":" + str(record.get("date_received"))),
                connector_name=self.name,
                operation="upsert",
                external_id=identity,
                payload=record,
                acl_scopes=[f"mail:{record['account']}:{record['mailbox']}"],
            )
