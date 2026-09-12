"""Freshness fields must never assert something nobody checked.

An empirical audit had an agent read a user's legal document through the
shipped instructions and report that it "changed after indexing". Nothing had
changed: the source was a cloud placeholder with no local bytes, so
`source_changed_since_index` was computed as `None != indexed_sha256` and came
out `true`. These tests pin the three halves of the repair: the unknown case is
`null`, every consumer treats `null` as unverified rather than fresh, and
`xlsx-read` reports the same verification field the other reopen paths do.
"""

from __future__ import annotations

import json

import anyio
import pytest
from openpyxl import Workbook

from kip.domain.models import AnswerRequest, SearchRequest


def _indexed_unit(test_container, name: str, body: str) -> tuple[object, str]:
    source = test_container.settings.project_root / "source" / name
    source.write_text(body, encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    hit = test_container.application.retrieval.search(
        context, SearchRequest(query=body.split()[0])
    )[0]
    return context, hit.unit_id


def test_unreadable_source_reports_unknown_instead_of_changed(test_container, monkeypatch):
    context, unit_id = _indexed_unit(test_container, "계약서.txt", "계약상대방 한빛전자")

    # A cloud placeholder holds no local bytes: nothing can be compared.
    monkeypatch.setattr("kip.adapters.storage.local.is_cloud_placeholder", lambda _stat: True)

    read = test_container.application.evidence.read_unit(context, unit_id)

    assert read.source_verification == "unavailable"
    assert read.current_source_sha256 is None
    # `null`, not `true`: reporting `true` is a false claim that the user's
    # document changed after indexing.
    assert read.source_changed_since_index is None


def test_context_items_carry_the_unknown_freshness_value_too(test_container, monkeypatch):
    from kip.domain.models import ContextRequest

    context, _unit_id = _indexed_unit(test_container, "규정.txt", "내부규정 열람")
    monkeypatch.setattr("kip.adapters.storage.local.is_cloud_placeholder", lambda _stat: True)

    bundle = test_container.application.retrieval.context_bundle(
        context, ContextRequest(query="내부규정", limit=1)
    )

    assert bundle.items[0].source_changed_since_index is None
    assert bundle.items[0].source_verification == "unavailable"


def test_answer_treats_unverifiable_evidence_as_stale_not_fresh(test_container, monkeypatch):
    """`null` must not read as "fresh" in the one consumer that used truthiness."""
    context, _unit_id = _indexed_unit(test_container, "합의서.txt", "합의당사자 한빛전자")
    monkeypatch.setattr("kip.adapters.storage.local.is_cloud_placeholder", lambda _stat: True)

    response = test_container.application.answering.answer(
        context, AnswerRequest(query="합의당사자는 누구인가?", limit=5)
    )

    assert response.refused is True
    assert response.refusal_reason == "no_fresh_evidence"
    assert response.citations == []


def _workbook(test_container) -> tuple[object, str]:
    path = test_container.settings.project_root / "source" / "정산.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "항목"
    sheet["B1"] = "금액"
    sheet["A2"] = "정산합계"
    sheet["B2"] = 1200000
    workbook.save(path)
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    hit = test_container.application.retrieval.search(
        context, SearchRequest(query="정산합계")
    )[0]
    return context, hit.artifact_id


def test_xlsx_read_reports_source_verification_like_read(test_container):
    context, artifact_id = _workbook(test_container)

    result = test_container.application.evidence.read_xlsx(
        context, artifact_id, sheet="Sheet", cell_range="A1:B2"
    )

    # Without this a caller cannot tell a verified cell value from an
    # unverified one, although `read` on the same artifact says so.
    assert result.source_verification == "sha256"
    assert result.source_changed_since_index is False


def test_xlsx_read_envelope_exposes_source_verification(test_container, monkeypatch):
    from typer.testing import CliRunner

    from kip.cli import app

    context, artifact_id = _workbook(test_container)
    assert context is not None
    monkeypatch.setattr("kip.cli.build_container", lambda settings, load_models=True: test_container)
    monkeypatch.setenv("KIP_WORKSPACE", "default")

    result = CliRunner().invoke(
        app, ["xlsx-read", artifact_id, "--sheet", "Sheet", "--range", "A1:B2"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["source_verification"] == "sha256"


def test_capabilities_reports_warnings_where_every_instruction_points(test_container, monkeypatch):
    from fastapi.testclient import TestClient
    from mcp.client import Client
    from typer.testing import CliRunner

    from kip.api import create_app
    from kip.cli import app
    from kip.mcp_server import create_server

    monkeypatch.setattr("kip.cli.build_container", lambda settings, load_models=True: test_container)
    monkeypatch.setattr("kip.mcp_server.build_container", lambda: test_container)
    monkeypatch.setenv("KIP_WORKSPACE", "default")

    cli = CliRunner().invoke(app, ["capabilities"])
    assert cli.exit_code == 0, cli.output
    with TestClient(create_app(test_container)) as client:
        rest = client.get("/v1/capabilities", headers={"X-KIP-API-Key": "test-key"})
        assert rest.status_code == 200

    async def invoke():
        async with Client(create_server(test_container)) as mcp_client:
            result = await mcp_client.call_tool("kip_capabilities", {})
            return json.loads(result.content[0].text)

    for envelope in (json.loads(cli.output), rest.json(), anyio.run(invoke)):
        assert envelope["ok"] is True
        assert envelope["data"]["warnings"], envelope
        # Documented place: every instruction tells a caller to read
        # `meta.warnings`, so the same warnings must be there.
        assert envelope["meta"]["warnings"] == envelope["data"]["warnings"]


@pytest.mark.parametrize(
    "name,field,expected",
    [
        ("evidence-read", "source_changed_since_index", "null is unknown, never fresh"),
        ("context-bundle", "source_changed_since_index", "null is unknown, never fresh"),
        ("xlsx-range-read", "source_verification", "Always sha256"),
    ],
)
def test_generated_contracts_describe_the_freshness_fields(name, field, expected):
    from pathlib import Path

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts" / f"{name}.schema.json").read_text()
    )
    rendered = json.dumps(schema, ensure_ascii=False)
    assert field in rendered
    assert expected in rendered


def test_a_direct_read_never_reports_stat_and_the_contract_says_so(test_container):
    from pathlib import Path

    from kip.domain.models import SearchRequest

    source = test_container.settings.project_root / "source" / "stat.txt"
    source.write_text("스탯재사용 자료")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    unit_id = test_container.application.retrieval.search(
        context, SearchRequest(query="스탯재사용")
    )[0].unit_id

    # Same unit, same untouched file: only the bulk reopen path reuses stat.
    assert (
        test_container.application.evidence.read_unit(context, unit_id).source_verification
        == "sha256"
    )
    assert (
        test_container.application.evidence.read_unit(
            context, unit_id, verify_hash=False
        ).source_verification
        == "stat"
    )

    # Every edge calls `read` with the verifying default, so a client reading
    # only the contract must not be left expecting a `stat` it cannot get.
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts" / "evidence-read.schema.json").read_text()
    )
    description = schema["properties"]["source_verification"]["description"]
    assert "A direct read always re-hashes" in description
    assert "never stat" in description


def test_xlsx_range_read_contract_publishes_the_only_verification_it_can_return():
    from pathlib import Path

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts" / "xlsx-range-read.schema.json").read_text()
    )
    field = schema["properties"]["source_verification"]

    # The path fails closed on an unreadable workbook, so a three-value type
    # with a default of "unavailable" published a state the service cannot
    # produce and contradicted the docstring two lines above it. Declared the
    # way `SearchHit.source_verification` declares `not_checked`.
    assert field.get("const") == "sha256" or field.get("enum") == ["sha256"]
    assert "default" not in field
    assert "source_verification" in schema["required"]


def test_search_hit_metadata_schema_scopes_is_latest_to_one_document():
    from pathlib import Path

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts" / "search-hit.schema.json").read_text()
    )
    description = schema["properties"]["metadata"]["description"]

    # A generated client sees only this description for a free-form metadata
    # map, so it has to say that is_latest is per logical document.
    assert "is_latest" in description
    assert "SAME logical document" in description
    assert "defaults to true" in description
