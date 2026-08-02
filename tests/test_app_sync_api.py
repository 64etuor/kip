from fastapi.testclient import TestClient

from kip.api import create_app


def test_application_can_enqueue_enabled_source_and_poll_jobs(test_container):
    client = TestClient(create_app(test_container))
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Admin-Key": "test-admin",
        "X-KIP-Workspace": "default",
        "X-KIP-Principal": "scheduler-app",
        "X-KIP-ACL-Scopes": "workspace:default",
    }
    response = client.post("/v1/sync/fixture", headers=headers)
    assert response.status_code == 200
    job_id = response.json()["data"]["job_id"]

    jobs = client.get("/v1/jobs", headers=headers, params={"status": "queued"})
    assert jobs.status_code == 200
    assert any(item["id"] == job_id for item in jobs.json()["data"])


def test_application_cannot_enqueue_unknown_source(test_container):
    client = TestClient(create_app(test_container))
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Admin-Key": "test-admin",
        "X-KIP-Workspace": "default",
        "X-KIP-ACL-Scopes": "workspace:default",
    }
    response = client.post("/v1/sync/not-configured", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
