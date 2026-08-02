"""Minimal application connector that submits a normalized change to KIP."""
from __future__ import annotations

import os
from datetime import UTC, datetime

from sdk.python.kip_client import KipClient


client = KipClient(
    base_url=os.environ.get("KIP_API_URL", "http://127.0.0.1:8080"),
    api_key=os.environ["KIP_API_KEY"],
    admin_key=os.environ["KIP_ADMIN_KEY"],
    workspace=os.environ.get("KIP_WORKSPACE", "default"),
    principal_id="connector-custom-crm",
    acl_scopes=["workspace:default", "project:A"],
)

external_id = "message-123"
event = {
    "schema_version": "kip.connector-event.v1",
    "event_id": f"custom-crm:{external_id}:v1",
    "connector_name": "custom-crm",
    "operation": "upsert",
    "external_id": external_id,
    "occurred_at": datetime.now(UTC).isoformat(),
    "payload": {
        "source_kind": "crm",
        "subject": "A과제 협약 변경",
        "text": "협약 변경 신청서를 제출했습니다.",
    },
    "acl_scopes": ["workspace:default", "project:A"],
}

print(client.post_connector_event(event))
