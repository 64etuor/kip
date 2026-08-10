from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from kip.domain.egress import DataClassification
from kip.domain.identity import AclSnapshot
from kip.domain.models import ConnectorEvent, DocumentPacket, IngestResult, RequestContext
from kip.ports.parser import ParserPort


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    path: Path
    relative_path: str
    size: int
    mtime_ns: int
    sha256: str


class FilesystemSourcePort(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def root(self) -> Path: ...

    @property
    def acl_scope(self) -> str | None: ...

    @property
    def acl_snapshot(self) -> AclSnapshot: ...

    @property
    def classification(self) -> DataClassification: ...

    def scan(self) -> Iterable[DiscoveredFile]: ...


class SourceCatalogPort(Protocol):
    def capabilities(self) -> dict[str, str]: ...

    def enabled_names(self) -> list[str]: ...

    def filesystem(self, source_name: str) -> FilesystemSourcePort: ...

    def events(
        self,
        source_name: str,
        *,
        since: str | None = None,
    ) -> Iterable[ConnectorEvent]: ...

    def event_acl_snapshot(self, event: ConnectorEvent) -> AclSnapshot: ...

    def event_classification(self, event: ConnectorEvent) -> DataClassification: ...


class ParserRegistryPort(Protocol):
    def find(self, path: Path) -> ParserPort: ...

    def capabilities(self) -> dict[str, str]: ...


class ContentAddressedStorePort(Protocol):
    def put(self, data: bytes, *, suffix: str = "") -> str: ...


class IngestionStore(Protocol):
    def upsert_acl_snapshot(
        self,
        context: RequestContext,
        source_object_id: str,
        snapshot: AclSnapshot,
        classification: DataClassification,
    ) -> None: ...

    def has_revision(
        self,
        context: RequestContext,
        source_object_id: str,
        sha256: str,
    ) -> bool: ...

    def ingest_packet(
        self,
        context: RequestContext,
        packet: DocumentPacket,
    ) -> IngestResult: ...

    def replace_extraction(
        self,
        context: RequestContext,
        packet: DocumentPacket,
    ) -> IngestResult: ...
