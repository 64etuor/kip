from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from kip.domain.models import ConnectorEvent


class SourceConnectorPort(Protocol):
    name: str
    kind: str

    def pull(self, cursor: dict | None = None) -> Iterable[ConnectorEvent]: ...
