from __future__ import annotations

import mimetypes
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kip.domain.egress import DataClassification
from kip.domain.identity import AclSnapshot
from kip.domain.models import (
    Artifact,
    DocumentPacket,
    IngestResult,
    LogicalDocument,
    RequestContext,
    SourceObject,
    SourceRevision,
)
from kip.errors import ConflictError, ValidationError
from kip.ids import stable_id
from kip.ports.evidence import SourceFileInspectorPort
from kip.ports.ingestion import DiscoveredFile, IngestionStore, ParserRegistryPort
from kip.ports.text_analyzer import TextAnalyzerPort

_VERSION_SUFFIX_RE = re.compile(
    r"(?:[ _.-]*(?:검색본|열람본|pdf|scan|scanned|원본파일))$",
    re.IGNORECASE,
)


def _safe_source_path(path: Path, source_root: Path) -> Path:
    resolved = path.resolve()
    root = source_root.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValidationError(f"path escaped configured source root: {path}")
    return resolved


def _logical_key(relative_path: str) -> str:
    path = Path(relative_path)
    stem = unicodedata.normalize("NFKC", path.stem).strip().lower()
    stem = _VERSION_SUFFIX_RE.sub("", stem).strip(" ._-")
    parent = unicodedata.normalize("NFKC", path.parent.as_posix()).strip().lower()
    return f"{parent}/{stem}" if parent not in {"", "."} else stem


def _representation_role(extension: str) -> str:
    return {
        ".hwp": "editable_original",
        ".hwpx": "editable_original",
        ".pdf": "searchable_representation",
        ".xlsx": "workbook",
        ".xlsm": "workbook",
        ".xls": "workbook",
    }.get(extension.lower(), "primary")


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    path: Path
    system_id: str
    object_id: str
    revision_id: str
    document_id: str
    artifact_id: str
    stable_key: str


@dataclass(frozen=True, slots=True)
class PreparedFilePacket:
    context: RequestContext
    packet: DocumentPacket


class FileIngestionWorkflow:
    def __init__(
        self,
        store: IngestionStore,
        parsers: ParserRegistryPort,
        analyzer: TextAnalyzerPort,
        source_files: SourceFileInspectorPort,
    ) -> None:
        self._store = store
        self._parsers = parsers
        self._analyzer = analyzer
        self._source_files = source_files

    def ingest(
        self,
        context: RequestContext,
        *,
        source_name: str,
        source_root: Path,
        record: DiscoveredFile,
        acl_scopes: list[str],
        acl_snapshot: AclSnapshot,
        classification: DataClassification,
    ) -> IngestResult:
        identity = self._identity(
            context,
            source_name=source_name,
            source_root=source_root,
            record=record,
        )
        ingest_context = self._ingest_context(context, acl_scopes)
        self._store.upsert_acl_snapshot(
            ingest_context,
            identity.object_id,
            acl_snapshot,
            classification,
        )
        if self._store.has_revision(
            ingest_context,
            identity.object_id,
            record.sha256,
        ):
            return IngestResult(
                status="unchanged",
                source_object_id=identity.object_id,
                revision_id=identity.revision_id,
                artifact_id=identity.artifact_id,
                document_id=identity.document_id,
                unit_count=0,
            )
        prepared = self._prepare(
            ingest_context,
            identity,
            source_name=source_name,
            source_root=source_root,
            record=record,
            acl_scopes=acl_scopes,
            acl_snapshot=acl_snapshot,
            classification=classification,
        )
        return self._store.ingest_packet(prepared.context, prepared.packet)

    def prepare_reextraction(
        self,
        context: RequestContext,
        *,
        source_name: str,
        source_root: Path,
        record: DiscoveredFile,
        acl_scopes: list[str],
        acl_snapshot: AclSnapshot,
        classification: DataClassification,
    ) -> PreparedFilePacket:
        identity = self._identity(
            context,
            source_name=source_name,
            source_root=source_root,
            record=record,
        )
        ingest_context = self._ingest_context(context, acl_scopes)
        if not self._store.has_revision(
            ingest_context,
            identity.object_id,
            record.sha256,
        ):
            raise ConflictError("source revision is not currently indexed")
        return self._prepare(
            ingest_context,
            identity,
            source_name=source_name,
            source_root=source_root,
            record=record,
            acl_scopes=acl_scopes,
            acl_snapshot=acl_snapshot,
            classification=classification,
        )

    def activate_reextraction(
        self,
        prepared: PreparedFilePacket,
    ) -> IngestResult:
        source_path = prepared.packet.artifact.source_path
        if source_path is None:
            raise ValidationError("candidate artifact has no source path")
        self._require_source_hash(Path(source_path), prepared.packet.revision.sha256)
        return self._store.replace_extraction(prepared.context, prepared.packet)

    @staticmethod
    def _ingest_context(
        context: RequestContext,
        acl_scopes: list[str],
    ) -> RequestContext:
        return context.model_copy(
            update={
                "acl_scopes": sorted(set(context.acl_scopes).union(acl_scopes))
            }
        )

    @staticmethod
    def _identity(
        context: RequestContext,
        *,
        source_name: str,
        source_root: Path,
        record: DiscoveredFile,
    ) -> _FileIdentity:
        path = _safe_source_path(record.path, source_root)
        system_id = stable_id("srcsys", context.workspace, source_name)
        object_id = stable_id("srcobj", system_id, record.relative_path)
        revision_id = stable_id("rev", object_id, record.sha256)
        stable_key = _logical_key(record.relative_path)
        document_id = stable_id("ldoc", context.workspace, stable_key)
        return _FileIdentity(
            path=path,
            system_id=system_id,
            object_id=object_id,
            revision_id=revision_id,
            document_id=document_id,
            artifact_id=stable_id("art", revision_id, path.name),
            stable_key=stable_key,
        )

    def _prepare(
        self,
        ingest_context: RequestContext,
        identity: _FileIdentity,
        *,
        source_name: str,
        source_root: Path,
        record: DiscoveredFile,
        acl_scopes: list[str],
        acl_snapshot: AclSnapshot,
        classification: DataClassification,
    ) -> PreparedFilePacket:
        path = identity.path
        self._require_source_hash(path, record.sha256)
        source_modified_at = datetime.fromtimestamp(
            record.mtime_ns / 1_000_000_000,
            tz=UTC,
        )
        extension = path.suffix.lower()
        extraction, units = self._parsers.find(path).parse(
            path,
            artifact_id=identity.artifact_id,
            document_id=identity.document_id,
            acl_scopes=acl_scopes,
        )
        for unit in units:
            unit.acl_snapshot_id = acl_snapshot.id
            unit.classification = classification
            unit.lexical_text = self._analyzer.analyze(
                "\n".join(
                    (
                        unit.title or "",
                        unit.body_normalized,
                        path.name,
                        record.relative_path,
                    )
                )
            )
        packet = DocumentPacket(
            workspace_id=ingest_context.workspace,
            source_object=SourceObject(
                id=identity.object_id,
                system_id=identity.system_id,
                system_name=source_name,
                system_kind="filesystem",
                external_id=record.relative_path,
                object_type="file",
                canonical_uri=path.as_uri(),
                classification=classification,
                acl_scopes=acl_scopes,
                acl_snapshot=acl_snapshot,
                metadata={"relative_path": record.relative_path},
            ),
            revision=SourceRevision(
                id=identity.revision_id,
                object_id=identity.object_id,
                revision_key=record.sha256,
                sha256=record.sha256,
                size_bytes=record.size,
                source_modified_at=source_modified_at,
                raw_object_uri=path.as_uri(),
                metadata={"mtime_ns": record.mtime_ns},
            ),
            logical_document=LogicalDocument(
                id=identity.document_id,
                stable_key=identity.stable_key,
                title=path.stem,
                metadata={
                    "source_name": source_name,
                    "relative_path": record.relative_path,
                },
            ),
            artifact=Artifact(
                id=identity.artifact_id,
                revision_id=identity.revision_id,
                file_name=path.name,
                extension=extension,
                media_type=mimetypes.guess_type(path.name)[0]
                or "application/octet-stream",
                byte_size=record.size,
                sha256=record.sha256,
                source_path=str(path),
                representation_role=_representation_role(extension),
                metadata={"source_root": str(source_root.resolve())},
            ),
            extraction=extraction,
            units=units,
        )
        self._require_source_hash(path, record.sha256)
        return PreparedFilePacket(context=ingest_context, packet=packet)

    def _require_source_hash(self, path: Path, expected: str) -> None:
        if self._source_files.require_sha256(path) != expected:
            raise ConflictError("source changed while parser candidate was prepared")
