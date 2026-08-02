from fastapi.testclient import TestClient

from kip.api import create_app
from kip.domain.models import AssertionCandidate
from kip.ids import new_id


def test_rest_and_application_use_same_memory_state(test_container):
    path = test_container.settings.project_root / "source" / "안내.txt"
    path.write_text("정산 증빙 제출기한은 2026년 8월 15일이다.", encoding="utf-8")
    context = test_container.service.request_context()
    test_container.service.sync_filesystem(context, "fixture")

    client = TestClient(create_app(test_container))
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Workspace": "default",
        "X-KIP-ACL-Scopes": "workspace:default",
    }
    response = client.post(
        "/v1/search",
        headers=headers,
        json={"query": "정산 증빙 제출기한", "limit": 10},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]
    unit_id = payload["data"][0]["unit_id"]

    evidence = client.get(f"/v1/units/{unit_id}", headers=headers)
    assert evidence.status_code == 200
    assert "2026년 8월 15일" in evidence.json()["data"]["unit"]["body"]


def test_rest_explains_approved_assertion_with_evidence(test_container):
    path = test_container.settings.project_root / "source" / "승인.txt"
    path.write_text("A과제 참여율 변경을 승인한다.", encoding="utf-8")
    context = test_container.service.request_context()
    test_container.service.sync_filesystem(context, "fixture")
    unit_id = next(iter(test_container.repository.units))
    candidate = AssertionCandidate(
        id=new_id("cand"),
        subject_id="doc_new",
        predicate="amends",
        object_entity_id="doc_old",
        origin="test",
        ontology_version="core/1.0.0",
        evidence=[{"content_unit_id": unit_id}],
    )
    test_container.service.create_candidate(context, candidate)
    assertion = test_container.service.review_approve(context, candidate.id)

    client = TestClient(create_app(test_container))
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Workspace": "default",
        "X-KIP-ACL-Scopes": "workspace:default",
    }
    response = client.get(f"/v1/assertions/{assertion.id}/explain", headers=headers)
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["assertion"]["id"] == assertion.id
    assert payload["evidence"][0]["unit"]["id"] == unit_id
