from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from kip.domain.models import ConnectorEvent


class SourceConnectorPort(Protocol):
    name: str
    kind: str

    def pull(self, cursor: dict[str, Any] | None = None) -> Iterable[ConnectorEvent]: ...
