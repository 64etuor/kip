from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ACL_SCOPE_COMMA_REASON = (
    "scopes are comma-separated in KIP_ACL_SCOPES, the X-KIP-ACL-Scopes header "
    "and the database session, so it would become separate scopes"
)
ROLE_COMMA_REASON = (
    "roles are comma-separated in KIP_ROLES and the database session, "
    "so it would become separate roles"
)


def comma_acl_scopes_error(scopes: Iterable[str], *, subject: str) -> str | None:
    """Explain the scopes that hold a comma, or None when every scope is comma-free.

    The database session splits `kip.acl_scopes` on commas, so a scope written
    with one would silently become several.
    """
    bad = [scope for scope in scopes if "," in scope]
    if not bad:
        return None
    listed = ", ".join(repr(scope) for scope in bad)
    return f"{subject} {listed} contains a comma, and an ACL scope cannot: {ACL_SCOPE_COMMA_REASON}"


def comma_roles_error(roles: Iterable[str], *, subject: str) -> str | None:
    """Explain the roles that hold a comma, or None when every role is comma-free.

    The database session splits `kip.roles` on commas, so a role written with
    one would silently become several, including a false `admin`.
    """
    bad = [role for role in roles if "," in role]
    if not bad:
        return None
    listed = ", ".join(repr(role) for role in bad)
    return f"{subject} {listed} contains a comma, and a role cannot: {ROLE_COMMA_REASON}"


def session_acl_scopes_value(scopes: Iterable[str]) -> str:
    """Join scopes for `set_config('kip.acl_scopes', ...)`, or raise.

    Last-line defense: a comma inside one scope would become two GUC values.
    """
    materialized = list(scopes)
    error = comma_acl_scopes_error(materialized, subject="session acl_scope")
    if error is not None:
        from kip.errors import ValidationError

        raise ValidationError(error)
    return ",".join(materialized)


def session_roles_value(roles: Iterable[str]) -> str:
    """Join roles for `set_config('kip.roles', ...)`, or raise.

    Last-line defense: a comma inside one role would become two GUC values,
    and `kip.current_is_admin()` is `'admin' = ANY(kip.current_roles())`.
    """
    materialized = list(roles)
    error = comma_roles_error(materialized, subject="session role")
    if error is not None:
        from kip.errors import ValidationError

        raise ValidationError(error)
    return ",".join(sorted(set(materialized)))


class IdentityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AclSnapshot(IdentityModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    scopes: list[str] = Field(default_factory=list)
    captured_at: datetime
    expires_at: datetime | None = None
    configuration_owned: bool = False

    @field_validator("scopes")
    @classmethod
    def normalize_scopes(cls, scopes: list[str]) -> list[str]:
        normalized = [scope.strip() for scope in scopes if scope.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("ACL snapshot scopes must be unique")
        error = comma_acl_scopes_error(normalized, subject="ACL snapshot scope")
        if error is not None:
            raise ValueError(error)
        return normalized

    @model_validator(mode="after")
    def validate_lifetime(self) -> AclSnapshot:
        if self.configuration_owned:
            if self.expires_at is not None:
                raise ValueError("configuration-owned ACL snapshots must not expire")
            return self
        if self.expires_at is None:
            raise ValueError("dynamic ACL snapshots require an expiry")
        if self.expires_at <= self.captured_at:
            raise ValueError("ACL snapshot expiry must be after capture time")
        return self

    def is_fresh(self, now: datetime | None = None) -> bool:
        selected_now = now or datetime.now(UTC)
        if self.captured_at > selected_now:
            return False
        return self.configuration_owned or (
            self.expires_at is not None and self.expires_at > selected_now
        )

    @classmethod
    def configuration(
        cls,
        *,
        snapshot_id: str,
        version: str,
        provider: str,
        scopes: list[str],
        captured_at: datetime | None = None,
    ) -> AclSnapshot:
        return cls(
            id=snapshot_id,
            version=version,
            provider=provider,
            scopes=scopes,
            captured_at=captured_at or datetime.now(UTC),
            configuration_owned=True,
        )


class IdentityCredential(IdentityModel):
    api_key: str | None = Field(default=None, repr=False)
    bearer_token: str | None = Field(default=None, repr=False)
    asserted_workspace: str | None = None
    asserted_principal_id: str | None = None
    asserted_acl_scopes: tuple[str, ...] = ()
