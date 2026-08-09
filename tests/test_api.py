from fastapi.testclient import TestClient
from openpyxl import Workbook

from kip.api import create_app
from kip.domain.models import AssertionCandidate
from kip.ids import new_id


def test_rest_and_application_use_same_memory_state(test_container):
    path = test_container.settings.project_root / "source" / "안내.txt"
    path.write_text("정산 증빙 제출기한은 2026년 8월 15일이다.", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")

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


def test_rest_answer_uses_exact_fresh_evidence(test_container):
    path = test_container.settings.project_root / "source" / "제출기한.txt"
    path.write_text("정산 증빙 제출기한은 2026년 8월 15일이다.", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    client = TestClient(create_app(test_container))
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Workspace": "default",
        "X-KIP-ACL-Scopes": "workspace:default",
    }

    response = client.post(
        "/v1/answer",
        headers=headers,
        json={"query": "정산 증빙 제출기한", "limit": 5},
    )

    assert response.status_code == 200
    answer = response.json()["data"]
    assert answer["refused"] is False
    assert "2026년 8월 15일" in answer["answer"]
    assert answer["citations"][0]["source_changed_since_index"] is False


def test_rest_answer_refuses_when_only_evidence_is_stale(test_container):
    path = test_container.settings.project_root / "source" / "승인상태.txt"
    path.write_text("A과제 참여율 변경은 승인되었다.", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    path.write_text("A과제 참여율 변경은 아직 검토 중이다.", encoding="utf-8")
    client = TestClient(create_app(test_container))
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Workspace": "default",
        "X-KIP-ACL-Scopes": "workspace:default",
    }

    response = client.post(
        "/v1/answer",
        headers=headers,
        json={"query": "A과제 참여율 변경 승인", "limit": 5},
    )

    assert response.status_code == 200
    answer = response.json()["data"]
    assert answer["refused"] is True
    assert answer["refusal_reason"] == "no_fresh_evidence"
    assert answer["citations"] == []


def test_read_marks_missing_source_as_changed(test_container):
    path = test_container.settings.project_root / "source" / "삭제됨.txt"
    path.write_text("삭제 전 원본", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    unit_id = next(iter(test_container.repository.units))
    path.unlink()

    evidence = test_container.application.evidence.read_unit(context, unit_id)

    assert evidence.current_source_sha256 is None
    assert evidence.source_changed_since_index is True


def test_rest_answer_requires_exact_xlsx_read_for_numeric_claim(test_container):
    path = test_container.settings.project_root / "source" / "정산.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "정산"
    sheet.append(["항목", "금액"])
    sheet.append(["인건비", 1500000])
    workbook.save(path)
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    client = TestClient(create_app(test_container))
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Workspace": "default",
        "X-KIP-ACL-Scopes": "workspace:default",
    }

    response = client.post(
        "/v1/answer",
        headers=headers,
        json={"query": "인건비 금액", "limit": 5},
    )

    answer = response.json()["data"]
    assert answer["refused"] is True
    assert answer["refusal_reason"] == "exact_xlsx_read_required"
    assert answer["citations"] == []


def test_rest_answer_refuses_without_authorized_evidence(test_container):
    path = test_container.settings.project_root / "source" / "비공개.txt"
    path.write_text("비공개 승인 금액은 900만원이다.", encoding="utf-8")
    owner = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(owner, "fixture")
    client = TestClient(create_app(test_container))
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Workspace": "default",
        "X-KIP-ACL-Scopes": "project:other",
    }

    response = client.post(
        "/v1/answer",
        headers=headers,
        json={"query": "비공개 승인 금액", "limit": 5},
    )

    answer = response.json()["data"]
    assert answer["refused"] is True
    assert answer["refusal_reason"] == "no_admissible_evidence"
    assert answer["citations"] == []


def test_rest_answer_refuses_common_question_words_without_domain_evidence(test_container):
    path = test_container.settings.project_root / "source" / "무관한안내.txt"
    path.write_text("언제까지 확인해야 하는 품질관리 문서인지 안내한다.", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    client = TestClient(create_app(test_container))
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Workspace": "default",
        "X-KIP-ACL-Scopes": "workspace:default",
    }

    response = client.post(
        "/v1/answer",
        headers=headers,
        json={"query": "정산 증빙은 언제까지 내야 해?", "limit": 5},
    )

    answer = response.json()["data"]
    assert answer["refused"] is True
    assert answer["refusal_reason"] == "no_admissible_evidence"
    assert answer["citations"] == []


def test_rest_answer_refuses_to_infer_approval_from_discussion_memo(test_container):
    path = test_container.settings.project_root / "source" / "변경메모.txt"
    path.write_text(
        "A과제 참여율 변경 논의가 있었다. 공식 효력은 승인 공문을 확인해야 한다.",
        encoding="utf-8",
    )
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    client = TestClient(create_app(test_container))
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Workspace": "default",
        "X-KIP-ACL-Scopes": "workspace:default",
    }

    response = client.post(
        "/v1/answer",
        headers=headers,
        json={"query": "A과제 참여율 변경이 승인됐어?", "limit": 5},
    )

    answer = response.json()["data"]
    assert answer["refused"] is True
    assert answer["refusal_reason"] == "insufficient_decision_evidence"
    assert len(answer["citations"]) == 1


def test_rest_answer_does_not_cite_generic_approval_document(test_container):
    source = test_container.settings.project_root / "source"
    subject_evidence = source / "A과제메모.txt"
    subject_evidence.write_text(
        "A과제 참여율 조정이 논의되었다. 공식 승인 문서를 확인해야 한다.",
        encoding="utf-8",
    )
    generic_policy = source / "일반승인절차.txt"
    generic_policy.write_text(
        "교육 참여율 변경은 담당자 승인이 필요하다. 변경 후 승인 기록을 보관한다.",
        encoding="utf-8",
    )
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    client = TestClient(create_app(test_container))
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Workspace": "default",
        "X-KIP-ACL-Scopes": "workspace:default",
    }

    response = client.post(
        "/v1/answer",
        headers=headers,
        json={"query": "A과제 참여율 변경이 승인됐어?", "limit": 10},
    )

    citations = response.json()["data"]["citations"]
    assert len(citations) == 1
    assert "A%EA%B3%BC%EC%A0%9C%EB%A9%94%EB%AA%A8" in citations[0]["source_uri"]


def test_rest_explains_approved_assertion_with_evidence(test_container):
    path = test_container.settings.project_root / "source" / "승인.txt"
    path.write_text("A과제 참여율 변경을 승인한다.", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
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
    test_container.application.knowledge.create_candidate(context, candidate)
    assertion = test_container.application.knowledge.review_approve(context, candidate.id)

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
