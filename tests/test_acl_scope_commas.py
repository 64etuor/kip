"""A single ACL scope never contains a comma, on every edge that accepts one.

Scope lists travel comma-separated through KIP_ACL_SCOPES, the
X-KIP-ACL-Scopes header and the PostgreSQL session (`kip.acl_scopes`), so a
scope written with a comma would silently become several.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError

from kip.adapters.identity.api_key import ApiKeyIdentityAdapter
from kip.adapters.identity.jwt import JwtIdentityAdapter, JwtIdentityConfig
from kip.adapters.repository.postgres import database as postgres_database
from kip.api import create_app
from kip.domain.identity import (
    IdentityCredential,
    session_acl_scopes_value,
    session_roles_value,
)
from kip.domain.knowledge import KnowledgeEntity
from kip.domain.models import RequestContext
from kip.errors import AuthorizationError, ConfigurationError, ValidationError, http_status
from kip.evaluation.models import GoldenCase
from kip.settings import Settings

REASON = (
    "contains a comma, and an ACL scope cannot: scopes are comma-separated in KIP_ACL_SCOPES, "
    "the X-KIP-ACL-Scopes header and the database session, so it would become separate scopes"
)
HEADERS = {"X-KIP-API-Key": "test-key", "X-KIP-Admin-Key": "test-admin"}


def _client(test_container) -> TestClient:
    identity = ApiKeyIdentityAdapter(
        expected_api_key="test-key",
        workspace="default",
        principal_id="connector-app",
        acl_scopes=("workspace:default",),
    )
    return TestClient(create_app(replace(test_container, identity=identity)))


def test_entity_creation_rejects_a_comma_scope_for_every_edge(test_container) -> None:
    # CLI, REST and the kip_ontology_entity_create MCP tool all call this service
    # with the scopes unsplit.
    admin = RequestContext(workspace="default", roles=["admin"])
    entity = KnowledgeEntity(
        id="ent_comma", entity_type="Project", canonical_name="쉼표", acl_scopes=["project:a", "group:a,b"],
    )

    with pytest.raises(ValidationError) as raised:
        test_container.application.ontology_rag.create_entity(admin, entity)

    assert str(raised.value) == f"entity acl_scope 'group:a,b' {REASON}"


def test_mcp_entity_creation_returns_validation_error_for_a_comma_scope(test_container, monkeypatch) -> None:
    import json

    import anyio
    from mcp.types import TextContent

    from kip.mcp_server import create_server

    monkeypatch.setattr("kip.mcp_server.build_container", lambda: test_container)
    # MCP takes its identity from the process environment, never tool arguments.
    monkeypatch.setenv("KIP_WORKSPACE", "default")
    monkeypatch.setenv("KIP_ROLES", "admin")
    server = create_server()

    async def invoke() -> dict:
        result = await server.call_tool(
            "kip_ontology_entity_create",
            {"entity_id": "ent_comma", "entity_type": "Project", "canonical_name": "쉼표", "acl_scopes": ["group:a,b"]},
        )
        assert isinstance(result.content[0], TextContent)
        return json.loads(result.content[0].text)

    envelope = anyio.run(invoke)

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "validation_error"
    assert envelope["error"]["message"] == f"entity acl_scope 'group:a,b' {REASON}"


def test_rest_entity_creation_returns_validation_error_for_a_comma_scope(test_container) -> None:
    response = _client(test_container).post(
        "/v1/ontology/entities",
        headers=HEADERS,
        json={"id": "ent_comma", "entity_type": "Project", "canonical_name": "쉼표", "acl_scopes": ["group:a,b"]},
    )

    assert response.status_code == http_status(ValidationError("comma"))
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == f"entity acl_scope 'group:a,b' {REASON}"


def test_rest_connector_event_returns_validation_error_for_a_comma_scope(test_container) -> None:
    client = _client(test_container)
    event = {
        "schema_version": "kip.connector-event.v1",
        "event_id": "evt_comma",
        "connector_name": "custom-crm",
        "operation": "upsert",
        "external_id": "message-comma",
        "payload": {"source_kind": "crm", "subject": "쉼표 범위", "text": "쉼표가 들어간 범위"},
        "acl_scopes": ["workspace:default", "project:A,B"],
    }

    response = client.post("/v1/connectors/events", headers=HEADERS, json=event)

    assert response.status_code == http_status(ValidationError("comma"))
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == f"connector event acl_scope 'project:A,B' {REASON}"
    # Nothing was ingested under the split scopes.
    search = client.post("/v1/search", headers=HEADERS, json={"query": "쉼표가 들어간 범위", "limit": 5})
    assert search.status_code == 200 and search.json()["data"] == []


def test_normal_workspace_group_and_project_scopes_are_accepted() -> None:
    context = RequestContext(
        workspace="acme",
        acl_scopes=["workspace:acme", "group:finance", "project:A"],
    )
    assert session_acl_scopes_value(context.acl_scopes) == "workspace:acme,group:finance,project:A"


def test_request_context_rejects_a_comma_scope() -> None:
    with pytest.raises(PydanticValidationError, match="acl_scope 'group:a,b'"):
        RequestContext(acl_scopes=["workspace:default", "group:a,b"])


def test_session_acl_scopes_value_is_the_last_line_before_the_database_guc() -> None:
    # Bypass RequestContext validation the way a buggy adapter could.
    with pytest.raises(ValidationError, match="session acl_scope 'group:a,b'"):
        session_acl_scopes_value(["workspace:default", "group:a,b"])


def test_request_context_rejects_a_comma_role() -> None:
    with pytest.raises(PydanticValidationError, match="role 'x,admin'"):
        RequestContext(roles=["x,admin"])


def test_session_roles_value_rejects_a_comma_role() -> None:
    with pytest.raises(ValidationError, match="session role 'x,admin'"):
        session_roles_value(["auditor", "x,admin"])


def test_session_roles_value_joins_unique_sorted_roles() -> None:
    assert session_roles_value(["admin", "auditor", "admin"]) == "admin,auditor"


def test_postgres_connection_refuses_comma_scope_or_role_before_set_config() -> None:
    # Deleting session_*_value(...) from _connection must fail this test.
    from kip.adapters.repository.postgres.database import PostgresDatabase

    executed: list[tuple[object, object]] = []

    class FakeCursor:
        def execute(self, sql: object, params: object = None) -> None:
            executed.append((sql, params))

        def __enter__(self) -> FakeCursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeConnection:
        def cursor(self) -> FakeCursor:
            return FakeCursor()

    class FakePooled:
        def __enter__(self) -> FakeConnection:
            return FakeConnection()

        def __exit__(self, *_args: object) -> bool:
            return False

    class FakePool:
        def connection(self) -> FakePooled:
            return FakePooled()

    database = PostgresDatabase("postgresql://unused")
    database._pool = FakePool()

    comma_scope = RequestContext.model_construct(
        workspace="default",
        principal_id="principal_local",
        acl_scopes=["workspace:default", "group:a,b"],
        roles=[],
    )
    with pytest.raises(ValidationError, match="session acl_scope 'group:a,b'"), database._connection(comma_scope):
        pass
    assert all("kip.acl_scopes" not in str(sql) for sql, _params in executed)

    executed.clear()
    comma_role = RequestContext.model_construct(
        workspace="default",
        principal_id="principal_local",
        acl_scopes=["workspace:default"],
        roles=["x,admin"],
    )
    with pytest.raises(ValidationError, match="session role 'x,admin'"), database._connection(comma_role):
        pass
    assert all("kip.roles" not in str(sql) for sql, _params in executed)

    executed.clear()
    ok = RequestContext(acl_scopes=["workspace:default"], roles=["admin"])
    with database._connection(ok):
        pass
    guc = next(params for sql, params in executed if params and "kip.acl_scopes" in str(sql))
    assert guc[3] == "workspace:default"
    assert guc[4] == "admin"

    source = Path(postgres_database.__file__).read_text(encoding="utf-8")
    assert "session_acl_scopes_value(context.acl_scopes)" in source
    assert "session_roles_value(context.roles)" in source


def test_api_key_adapter_rejects_a_comma_scope_at_construction() -> None:
    with pytest.raises(ConfigurationError, match="API-key acl_scope 'group:a,b'"):
        ApiKeyIdentityAdapter(
            expected_api_key="key",
            workspace="acme",
            principal_id="operator",
            acl_scopes=("workspace:acme", "group:a,b"),
        )


def _jwt_adapter(secret: str) -> JwtIdentityAdapter:
    return JwtIdentityAdapter(
        JwtIdentityConfig(
            issuer="https://identity.example.test",
            audience="kip-api",
            jwks_url="https://identity.example.test/.well-known/jwks.json",
            algorithms=("HS256",),
        ),
        signing_key_provider=lambda _: secret,
        clock=lambda: datetime.now(UTC),
    )


def _jwt_token(secret: str, *, groups: list[str], scopes: list[str]) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": "https://identity.example.test",
            "aud": "kip-api",
            "sub": "user-42",
            "workspace": "acme",
            "groups": groups,
            "acl_scopes": scopes,
            "acl_snapshot_id": "aclsnap_42",
            "acl_snapshot_version": "directory-v19",
            "acl_snapshot_captured_at": int((now - timedelta(minutes=1)).timestamp()),
            "acl_snapshot_expires_at": int((now + timedelta(minutes=4)).timestamp()),
            "iat": int((now - timedelta(minutes=1)).timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        secret,
        algorithm="HS256",
        headers={"kid": "test-key"},
    )


def test_jwt_rejects_a_comma_in_a_group_name_or_scope_claim() -> None:
    secret = "a-test-signing-key-that-is-long-enough-for-hs256"
    adapter = _jwt_adapter(secret)

    with pytest.raises(AuthorizationError, match="JWT group or scope claim 'finance,legal'"):
        adapter.resolve(
            IdentityCredential(bearer_token=_jwt_token(secret, groups=["finance,legal"], scopes=[])),
            request_id="req_1",
        )
    with pytest.raises(AuthorizationError, match="JWT group or scope claim 'project:A,B'"):
        adapter.resolve(
            IdentityCredential(bearer_token=_jwt_token(secret, groups=["finance"], scopes=["project:A,B"])),
            request_id="req_2",
        )


def test_jwt_accepts_workspace_group_and_project_scopes() -> None:
    secret = "a-test-signing-key-that-is-long-enough-for-hs256"
    context = _jwt_adapter(secret).resolve(
        IdentityCredential(
            bearer_token=_jwt_token(secret, groups=["finance"], scopes=["project:A"]),
        ),
        request_id="req_ok",
    )

    assert context.acl_scopes == ["workspace:acme", "group:finance", "project:A"]


def test_settings_load_rejects_comma_scopes_in_api_key_sources_and_connector_policies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("KIP_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("KIP_ENV", "test")
    monkeypatch.setenv("KIP_DATABASE_URL", "memory://")
    config = tmp_path / "config" / "kip.toml"
    config.parent.mkdir()
    monkeypatch.setenv("KIP_CONFIG", str(config))

    config.write_text(
        'identity.api_key.acl_scopes = ["workspace:acme", "group:a,b"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match=r"identity.api_key.acl_scopes 'group:a,b'"):
        Settings.load()

    config.write_text(
        "[[sources.filesystem]]\n"
        'name = "docs"\n'
        'root = "/tmp/docs"\n'
        'acl_scope = "group:a,b"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match=r"sources.filesystem 'docs' acl_scope 'group:a,b'"):
        Settings.load()

    config.write_text(
        "[[sources.connector_policies]]\n"
        'name = "crm"\n'
        'acl_mode = "static"\n'
        'acl_scopes = ["workspace:acme", "project:A,B"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match=r"sources.connector_policies 'crm' acl_scopes 'project:A,B'"):
        Settings.load()


def test_evaluation_dataset_rejects_a_comma_acl_scope() -> None:
    with pytest.raises(PydanticValidationError, match="evaluation case acl_scope 'group:a,b'"):
        GoldenCase(
            id="GQ-COMMA",
            question="쉼표 범위",
            category="acl_denial",
            principal="principal_public",
            acl_scopes=["workspace:default", "group:a,b"],
            expected_documents=["ldoc_example"],
        )
