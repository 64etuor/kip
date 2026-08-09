from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import UTC, datetime

from kip.domain.identity import AclSnapshot, IdentityCredential
from kip.domain.models import RequestContext
from kip.errors import AuthorizationError, ConfigurationError
from kip.ids import stable_id


class ApiKeyIdentityAdapter:
    def __init__(
        self,
        *,
        expected_api_key: str,
        workspace: str,
        principal_id: str,
        acl_scopes: tuple[str, ...],
        roles: tuple[str, ...] = ("admin",),
        allow_anonymous: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not expected_api_key and not allow_anonymous:
            raise ConfigurationError("API-key identity mode requires KIP_API_KEY")
        self._expected_api_key = expected_api_key
        self._workspace = workspace
        self._principal_id = principal_id
        self._acl_scopes = tuple(dict.fromkeys(acl_scopes))
        self._roles = tuple(dict.fromkeys(roles))
        self._allow_anonymous = allow_anonymous
        self._clock = clock or (lambda: datetime.now(UTC))

    def resolve(
        self,
        credential: IdentityCredential,
        *,
        request_id: str,
    ) -> RequestContext:
        supplied = credential.api_key or ""
        if self._expected_api_key:
            if not hmac.compare_digest(supplied, self._expected_api_key):
                raise AuthorizationError("invalid API key")
        elif not self._allow_anonymous:
            raise AuthorizationError("API key is required")

        captured_at = self._clock()
        snapshot = AclSnapshot.configuration(
            snapshot_id=stable_id(
                "aclsnap",
                self._workspace,
                "\0".join(
                    ("api-key", self._principal_id, *self._acl_scopes)
                ),
            ),
            version="configuration-v1",
            provider="api-key",
            scopes=list(self._acl_scopes),
            captured_at=captured_at,
        )
        return RequestContext(
            workspace=self._workspace,
            principal_id=self._principal_id,
            acl_scopes=list(self._acl_scopes),
            request_id=request_id,
            acl_snapshot=snapshot,
            roles=list(self._roles),
        )
