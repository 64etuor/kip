from __future__ import annotations

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kip.api import create_app
from kip.cli import app
from kip.domain.models import AssertionCandidate
from kip.ids import new_id


def test_cli_and_rest_preserve_stable_edge_semantics(test_container, monkeypatch) -> None:
    # Given one indexed corpus and one approved, evidence-backed assertion
    path = test_container.settings.project_root / "source" / "승인.txt"
    path.write_text("A과제 참여율 변경을 승인한다.", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    unit_id = next(iter(test_container.repository.units))
    candidate = AssertionCandidate(
        id=new_id("cand"),
        subject_id="project_a_change",
        predicate="amends",
        object_entity_id="project_a_plan",
        origin="characterization",
        ontology_version="core/1.0.0",
        evidence=[{"content_unit_id": unit_id}],
    )
    test_container.application.knowledge.create_candidate(context, candidate)
    assertion = test_container.application.knowledge.review_approve(context, candidate.id)
    monkeypatch.setattr(
        "kip.cli.build_container",
        lambda settings, load_models=True: test_container,
    )
    runner = CliRunner()
    client = TestClient(create_app(test_container))
    headers = {
        "X-KIP-API-Key": "test-key",
        "X-KIP-Workspace": "default",
        "X-KIP-Principal": "principal_local",
        "X-KIP-ACL-Scopes": "workspace:default",
    }
    cases = [
        (["capabilities"], "get", "/v1/capabilities", None),
        (["search", "A과제 참여율 변경"], "post", "/v1/search", {"query": "A과제 참여율 변경"}),
        (["answer", "A과제 참여율 변경"], "post", "/v1/answer", {"query": "A과제 참여율 변경"}),
        (
            ["explain", "--assertion-id", assertion.id],
            "get",
            f"/v1/assertions/{assertion.id}/explain",
            None,
        ),
        (
            ["graph", "neighbors", "--node-id", "project_a_change"],
            "post",
            "/v1/graph/neighbors",
            {"node_id": "project_a_change"},
        ),
    ]

    # When equivalent requests pass through the CLI and REST adapters
    for cli_args, method, route, body in cases:
        cli_result = runner.invoke(app, cli_args)
        response = client.request(method, route, headers=headers, json=body)
        assert cli_result.exit_code == 0, cli_result.stdout
        assert response.status_code == 200, response.text
        cli_payload = json.loads(cli_result.stdout)
        rest_payload = response.json()

        # Then both edges expose the same versioned result and stable data
        assert cli_payload["schema_version"] == rest_payload["schema_version"]
        assert cli_payload["ok"] == rest_payload["ok"]
        assert cli_payload["data"] == rest_payload["data"]
        assert cli_payload["meta"]["workspace"] == rest_payload["meta"]["workspace"]
