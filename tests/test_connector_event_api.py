from dataclasses import replace

from fastapi.testclient import TestClient

from kip.adapters.identity.api_key import ApiKeyIdentityAdapter
from kip.api import create_app


def test_application_connector_event_ingests_through_same_service(test_container):
    connector_identity = ApiKeyIdentityAdapter(
        expected_api_key="test-key",
        workspace="default",
        principal_id="connector-app",
        acl_scopes=("workspace:default", "project:A"),
    )
    client = TestClient(
        create_app(replace(test_container, identity=connector_identity))
    )
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Admin-Key": "test-admin",
    }
    event = {
        "schema_version": "kip.connector-event.v1",
        "event_id": "evt_custom_1",
        "connector_name": "custom-crm",
        "operation": "upsert",
        "external_id": "message-1",
        "payload": {
            "source_kind": "crm",
            "subject": "A과제 변경 신청",
            "text": "참여율 변경 신청서를 제출했다.",
        },
        "acl_scopes": ["workspace:default", "project:A"],
    }
    response = client.post("/v1/connectors/events", headers=headers, json=event)
    assert response.status_code == 200

    result = client.post(
        "/v1/search",
        headers=headers,
        json={"query": "참여율 변경 신청", "limit": 10},
    )
    assert result.status_code == 200
    assert result.json()["data"][0]["source_kind"] == "crm"
