from __future__ import annotations

import builtins
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from kip.adapters.identity.api_key import ApiKeyIdentityAdapter
from kip.adapters.identity.jwt import JwtIdentityAdapter, JwtIdentityConfig
from kip.api import create_app
from kip.container import build_container
from kip.domain.identity import IdentityCredential
from kip.errors import AuthorizationError, DependencyUnavailableError
from kip.settings import Settings

NOW = datetime.now(UTC)


def test_api_key_identity_ignores_caller_identity_and_uses_configured_scope() -> None:
    adapter = ApiKeyIdentityAdapter(
        expected_api_key="correct-key",
        workspace="acme",
        principal_id="bootstrap-operator",
        acl_scopes=("workspace:acme", "group:knowledge-admins"),
        clock=lambda: NOW,
    )

    context = adapter.resolve(
        IdentityCredential(
            api_key="correct-key",
            asserted_workspace="victim",
            asserted_principal_id="root",
            asserted_acl_scopes=("workspace:victim", "group:admins"),
        ),
        request_id="req_1",
    )

    assert context.workspace == "acme"
    assert context.principal_id == "bootstrap-operator"
    assert context.acl_scopes == ["workspace:acme", "group:knowledge-admins"]
    assert context.acl_snapshot is not None
    assert context.acl_snapshot.configuration_owned is True


def test_api_key_identity_fails_closed() -> None:
    adapter = ApiKeyIdentityAdapter(
        expected_api_key="correct-key",
        workspace="acme",
        principal_id="bootstrap-operator",
        acl_scopes=("workspace:acme",),
    )

    with pytest.raises(AuthorizationError, match="invalid API key"):
        adapter.resolve(IdentityCredential(api_key="wrong-key"), request_id="req_1")


def test_jwt_identity_maps_verified_claims_and_fresh_acl_snapshot() -> None:
    secret = "a-test-signing-key-that-is-long-enough-for-hs256"
    token = jwt.encode(
        {
            "iss": "https://identity.example.test",
            "aud": "kip-api",
            "sub": "user-42",
            "workspace": "acme",
            "groups": ["finance", "knowledge-reviewers"],
            "acl_scopes": ["project:A"],
            "acl_snapshot_id": "aclsnap_42",
            "acl_snapshot_version": "directory-v19",
            "acl_snapshot_captured_at": int((NOW - timedelta(minutes=1)).timestamp()),
            "acl_snapshot_expires_at": int((NOW + timedelta(minutes=4)).timestamp()),
            "iat": int((NOW - timedelta(minutes=1)).timestamp()),
            "exp": int((NOW + timedelta(minutes=10)).timestamp()),
        },
        secret,
        algorithm="HS256",
        headers={"kid": "test-key"},
    )
    adapter = JwtIdentityAdapter(
        JwtIdentityConfig(
            issuer="https://identity.example.test",
            audience="kip-api",
            jwks_url="https://identity.example.test/.well-known/jwks.json",
            algorithms=("HS256",),
            admin_groups=("knowledge-reviewers",),
        ),
        signing_key_provider=lambda _: secret,
        clock=lambda: NOW,
    )

    context = adapter.resolve(
        IdentityCredential(bearer_token=token),
        request_id="req_42",
    )

    assert context.workspace == "acme"
    assert context.principal_id == "user-42"
    assert context.acl_scopes == [
        "workspace:acme",
        "group:finance",
        "group:knowledge-reviewers",
        "project:A",
    ]
    assert context.acl_snapshot is not None
    assert context.acl_snapshot.id == "aclsnap_42"
    assert context.acl_snapshot.is_fresh(NOW) is True
    assert context.roles == ["admin"]


def test_jwt_identity_rejects_stale_acl_snapshot_even_when_token_is_valid() -> None:
    secret = "a-test-signing-key-that-is-long-enough-for-hs256"
    token = jwt.encode(
        {
            "iss": "https://identity.example.test",
            "aud": "kip-api",
            "sub": "user-42",
            "workspace": "acme",
            "groups": [],
            "acl_scopes": [],
            "acl_snapshot_id": "aclsnap_stale",
            "acl_snapshot_version": "directory-v18",
            "acl_snapshot_captured_at": int((NOW - timedelta(hours=2)).timestamp()),
            "acl_snapshot_expires_at": int((NOW - timedelta(minutes=1)).timestamp()),
            "iat": int((NOW - timedelta(minutes=2)).timestamp()),
            "exp": int((NOW + timedelta(minutes=10)).timestamp()),
        },
        secret,
        algorithm="HS256",
        headers={"kid": "test-key"},
    )
    adapter = JwtIdentityAdapter(
        JwtIdentityConfig(
            issuer="https://identity.example.test",
            audience="kip-api",
            jwks_url="https://identity.example.test/.well-known/jwks.json",
            algorithms=("HS256",),
        ),
        signing_key_provider=lambda _: secret,
        clock=lambda: NOW,
    )

    with pytest.raises(AuthorizationError, match="ACL snapshot is stale"):
        adapter.resolve(IdentityCredential(bearer_token=token), request_id="req_42")


def test_jwt_identity_fails_fast_when_optional_dependency_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def import_without_jwt(
        name: str,
        globals_: object | None = None,
        locals_: object | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "jwt":
            raise ImportError("simulated minimal installation")
        return original_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_jwt)

    with pytest.raises(DependencyUnavailableError, match="identity extra"):
        JwtIdentityAdapter(
            JwtIdentityConfig(
                issuer="https://identity.example.test",
                audience="kip-api",
                jwks_url="https://identity.example.test/.well-known/jwks.json",
            )
        )


def test_production_api_rejects_legacy_identity_headers(tmp_path) -> None:
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "identity": {
                "mode": "api_key",
                "api_key": {
                    "principal_id": "bootstrap-operator",
                    "acl_scopes": ["workspace:acme"],
                },
            },
            "search": {"semantic_enabled": False},
            "graph": {"backend": "memory"},
        },
        environment="production",
        workspace="acme",
        database_url="memory://",
        cas_path=tmp_path / "cas",
        api_key="correct-key",
        admin_key="admin-key",
    )
    client = TestClient(create_app(build_container(settings)))

    response = client.get(
        "/v1/capabilities",
        headers={
            "X-KIP-API-Key": "correct-key",
            "X-KIP-Principal": "root",
            "X-KIP-ACL-Scopes": "workspace:other,group:admins",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "untrusted_identity_headers"
