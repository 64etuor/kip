from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

from kip.domain.identity import AclSnapshot, IdentityCredential
from kip.domain.models import RequestContext
from kip.errors import AuthorizationError, ConfigurationError, DependencyUnavailableError


@dataclass(frozen=True, slots=True)
class JwtIdentityConfig:
    issuer: str
    audience: str
    jwks_url: str
    algorithms: tuple[str, ...] = ("RS256",)
    principal_claim: str = "sub"
    workspace_claim: str = "workspace"
    group_claim: str = "groups"
    scope_claim: str = "acl_scopes"
    group_scope_prefix: str = "group:"
    admin_groups: tuple[str, ...] = ()
    snapshot_id_claim: str = "acl_snapshot_id"
    snapshot_version_claim: str = "acl_snapshot_version"
    snapshot_captured_at_claim: str = "acl_snapshot_captured_at"
    snapshot_expires_at_claim: str = "acl_snapshot_expires_at"
    jwks_cache_seconds: float = 300
    jwks_timeout_seconds: float = 5
    clock_skew_seconds: float = 30

    def __post_init__(self) -> None:
        if not self.issuer or not self.audience or not self.jwks_url:
            raise ConfigurationError("JWT issuer, audience, and JWKS URL are required")
        if not self.algorithms:
            raise ConfigurationError("JWT algorithm allow-list must not be empty")
        parsed = urlsplit(self.jwks_url)
        is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
            raise ConfigurationError("JWT JWKS URL must use HTTPS or loopback HTTP")
        if self.jwks_cache_seconds <= 0 or self.jwks_timeout_seconds <= 0:
            raise ConfigurationError("JWT JWKS cache and timeout must be positive")


class JwtIdentityAdapter:
    def __init__(
        self,
        config: JwtIdentityConfig,
        *,
        signing_key_provider: Callable[[str], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        try:
            import jwt
        except ImportError as exc:
            raise DependencyUnavailableError(
                "JWT identity mode requires the identity extra: pip install '.[identity]'"
            ) from exc

        self._jwt = jwt
        self._config = config
        self._clock = clock or (lambda: datetime.now(UTC))
        self._signing_key_provider: Callable[[str], Any]
        if signing_key_provider is None:
            client = jwt.PyJWKClient(
                config.jwks_url,
                cache_keys=False,
                cache_jwk_set=True,
                lifespan=config.jwks_cache_seconds,
                timeout=config.jwks_timeout_seconds,
            )
            self._signing_key_provider = lambda token: client.get_signing_key_from_jwt(
                token
            )
        else:
            self._signing_key_provider = signing_key_provider

    def resolve(
        self,
        credential: IdentityCredential,
        *,
        request_id: str,
    ) -> RequestContext:
        token = credential.bearer_token or ""
        if not token:
            raise AuthorizationError("bearer token is required")
        config = self._config
        required_claims = list(
            dict.fromkeys(
                (
                    "exp",
                    "iat",
                    "iss",
                    "aud",
                    config.principal_claim,
                    config.workspace_claim,
                    config.group_claim,
                    config.snapshot_id_claim,
                    config.snapshot_version_claim,
                    config.snapshot_captured_at_claim,
                    config.snapshot_expires_at_claim,
                )
            )
        )
        try:
            key = self._signing_key_provider(token)
            decode = cast(Callable[..., dict[str, Any]], self._jwt.decode)
            claims = decode(
                token,
                key=key,
                algorithms=list(config.algorithms),
                audience=config.audience,
                issuer=config.issuer,
                leeway=timedelta(seconds=config.clock_skew_seconds),
                options={"require": required_claims},
            )
        except Exception as exc:
            raise AuthorizationError("invalid bearer token") from exc

        workspace = _required_string(claims, config.workspace_claim)
        principal_id = _required_string(claims, config.principal_claim)
        groups = _string_list(claims, config.group_claim, required=True)
        direct_scopes = _string_list(claims, config.scope_claim, required=False)
        workspace_scope = f"workspace:{workspace}"
        for scope in direct_scopes:
            if scope.startswith("workspace:") and scope != workspace_scope:
                raise AuthorizationError("JWT contains a cross-workspace ACL scope")
        scopes = list(
            dict.fromkeys(
                [
                    workspace_scope,
                    *(f"{config.group_scope_prefix}{group}" for group in groups),
                    *direct_scopes,
                ]
            )
        )
        captured_at = _numeric_date(claims, config.snapshot_captured_at_claim)
        expires_at = _numeric_date(claims, config.snapshot_expires_at_claim)
        try:
            snapshot = AclSnapshot(
                id=_required_string(claims, config.snapshot_id_claim),
                version=_required_string(claims, config.snapshot_version_claim),
                provider=config.issuer,
                scopes=scopes,
                captured_at=captured_at,
                expires_at=expires_at,
            )
        except ValueError as exc:
            raise AuthorizationError("invalid ACL snapshot claims") from exc
        if not snapshot.is_fresh(self._clock()):
            raise AuthorizationError("ACL snapshot is stale")

        return RequestContext(
            workspace=workspace,
            principal_id=principal_id,
            acl_scopes=scopes,
            request_id=request_id,
            acl_snapshot=snapshot,
            roles=["admin"] if set(groups).intersection(config.admin_groups) else [],
        )


def _required_string(claims: dict[str, Any], claim: str) -> str:
    value = claims.get(claim)
    if not isinstance(value, str) or not value.strip():
        raise AuthorizationError(f"JWT claim is not a non-empty string: {claim}")
    return value.strip()


def _string_list(
    claims: dict[str, Any],
    claim: str,
    *,
    required: bool,
) -> list[str]:
    value = claims.get(claim)
    if value is None and not required:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise AuthorizationError(f"JWT claim is not a string list: {claim}")
    normalized = [item.strip() for item in value]
    if len(normalized) != len(set(normalized)):
        raise AuthorizationError(f"JWT claim contains duplicate values: {claim}")
    return normalized


def _numeric_date(claims: dict[str, Any], claim: str) -> datetime:
    value = claims.get(claim)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuthorizationError(f"JWT claim is not a NumericDate: {claim}")
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise AuthorizationError(f"JWT claim is not a valid NumericDate: {claim}") from exc
