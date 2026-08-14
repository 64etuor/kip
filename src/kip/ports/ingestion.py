from __future__ import annotations

import hashlib
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Protocol

from kip.domain.egress import DataClassification
from kip.domain.identity import AclSnapshot
from kip.domain.models import (
    ConnectorEvent,
    DocumentPacket,
    IngestResult,
    RequestContext,
    SourceObjectAbsence,
)
from kip.ports.parser import ParserPort


class DiscoveredFile:
    """A scanned source file whose content hash is computed on first use.

    Deferring the hash lets sync skip unchanged files by size/mtime without
    reading their bytes.
    """

    __slots__ = ("_sha256", "mtime_ns", "path", "relative_path", "size")

    def __init__(
        self,
        path: Path,
        relative_path: str,
        size: int,
        mtime_ns: int,
        sha256: str | None = None,
    ) -> None:
        self.path = path
        self.relative_path = relative_path
        self.size = size
        self.mtime_ns = mtime_ns
        self._sha256 = sha256

    @property
    def sha256(self) -> str:
        if self._sha256 is None:
            digest = hashlib.sha256()
            with self.path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            self._sha256 = digest.hexdigest()
        return self._sha256


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

    def scan(
        self,
        *,
        include_extensions: set[str] | None = None,
    ) -> Iterable[DiscoveredFile]: ...


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

    def current_revision_by_stat(
        self,
        context: RequestContext,
        source_object_id: str,
        *,
        size: int,
        mtime_ns: int,
    ) -> str | None: ...

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

    def reconcile_scan_absences(
        self,
        context: RequestContext,
        system_id: str,
        seen_object_ids: AbstractSet[str],
    ) -> list[SourceObjectAbsence]:
        """Record one complete scan's absence observations for a source system.

        Clears the absence mark on every seen object, increments the
        consecutive-absence counter on every active (non-tombstoned) object
        the scan did not see, and returns those absent objects with their
        updated counters. Callers must invoke this only after a COMPLETE,
        successful scan; it never tombstones by itself.
        """
        ...
