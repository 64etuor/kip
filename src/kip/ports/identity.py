from __future__ import annotations

from typing import Protocol

from kip.domain.identity import IdentityCredential
from kip.domain.models import RequestContext


class IdentityResolverPort(Protocol):
    def resolve(
        self,
        credential: IdentityCredential,
        *,
        request_id: str,
    ) -> RequestContext: ...
