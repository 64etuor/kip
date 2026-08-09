from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
