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
    ExtractionRun,
    IngestResult,
    LogicalDocument,
    RequestContext,
    SourceObject,
    SourceObjectAbsence,
    SourceRevision,
)
from kip.errors import ConflictError, ValidationError
from kip.ids import new_id, sha256_bytes, stable_id
from kip.ports.evidence import EvidenceStore, SourceFileInspectorPort
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
        evidence: EvidenceStore,
        parsers: ParserRegistryPort,
        analyzer: TextAnalyzerPort,
        source_files: SourceFileInspectorPort,
    ) -> None:
        self._store = store
        self._evidence = evidence
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
        ingest_context = self._ingest_context(context, acl_scopes)
        system_id = stable_id("srcsys", context.workspace, source_name)
        object_id = stable_id("srcobj", system_id, record.relative_path)
        path = _safe_source_path(record.path, source_root)
        current = self._store.current_source_revision(ingest_context, object_id)
        same_path = current is None or current.raw_object_uri == path.as_uri()
        # Never put the new root's authorization on bytes from the old root.
        # The normal packet commit refreshes policy after a changed-root file
        # has been parsed successfully in its new location.
        if same_path:
            self._store.upsert_acl_snapshot(
                ingest_context, object_id, acl_snapshot, classification,
            )
        # Fast path: if the stored current revision matches the file's
        # size/mtime, skip hashing (and therefore reading) the file entirely.
        current_revision_id = (
            current.id if current is not None and same_path
            and not current.is_tombstone
            and current.size_bytes == record.size
            and current.metadata.get("mtime_ns") == record.mtime_ns
            else None
        )
        if current_revision_id is not None:
            path = _safe_source_path(record.path, source_root)
            return IngestResult(
                status="unchanged",
                source_object_id=object_id,
                revision_id=current_revision_id,
                artifact_id=stable_id("art", current_revision_id, path.name),
                document_id=stable_id(
                    "ldoc",
                    context.workspace,
                    _logical_key(record.relative_path),
                ),
                unit_count=0,
            )
        identity = self._identity(
            ingest_context,
            source_name=source_name,
            source_root=source_root,
            record=record,
        )
        if current is not None and same_path and not current.is_tombstone and current.sha256 == record.sha256:
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
        current = self._evidence.get_artifact(
            ingest_context,
            identity.artifact_id,
        )
        current_source = current.source_object
        current_revision = current.revision
        if (
            current_source is None
            or current_source.acl_snapshot is None
            or current_revision is None
            or current_source.id != identity.object_id
            or current_revision.id != identity.revision_id
            or current_revision.sha256 != record.sha256
        ):
            raise ConflictError("source revision policy is not current")
        return self._prepare(
            ingest_context,
            identity,
            source_name=source_name,
            source_root=source_root,
            record=record,
            acl_scopes=current_source.acl_scopes,
            acl_snapshot=current_source.acl_snapshot,
            classification=current_source.classification,
        )

    def tombstone_absent(
        self,
        context: RequestContext,
        absence: SourceObjectAbsence,
    ) -> IngestResult:
        """Soft-delete an object confirmed absent by the grace policy.

        Reuses the event-connector deletion semantics: an immutable tombstone
        revision with zero content units is ingested through the same
        `ingest_packet` path, so the previous revision, extraction history,
        and canonical rows are preserved while the object leaves the active
        lexical/search projection. Nothing is hard-deleted and no source path
        is written.
        """
        view = self._evidence.get_artifact(context, absence.artifact_id)
        source_object = view.source_object
        revision = view.revision
        document = view.document
        if source_object is None or revision is None or document is None:
            raise ConflictError("source metadata missing for tombstone candidate")
        if revision.is_tombstone:
            return IngestResult(
                status="unchanged",
                source_object_id=source_object.id,
                revision_id=revision.id,
                artifact_id=view.artifact.id,
                document_id=document.id,
            )
        tombstone_hash = sha256_bytes(f"tombstone:{revision.sha256}".encode())
        revision_id = stable_id("rev", source_object.id, tombstone_hash)
        artifact_id = stable_id("art", revision_id, view.artifact.file_name)
        extraction_id = new_id("ext")
        packet = DocumentPacket(
            workspace_id=context.workspace,
            source_object=source_object.model_copy(deep=True),
            revision=SourceRevision(
                id=revision_id,
                object_id=source_object.id,
                revision_key=tombstone_hash,
                sha256=tombstone_hash,
                size_bytes=0,
                source_modified_at=None,
                raw_object_uri=revision.raw_object_uri,
                is_tombstone=True,
                metadata={
                    "tombstone_reason": "filesystem_absence",
                    "tombstoned_revision_id": revision.id,
                    "absent_scan_count": absence.absent_scan_count,
                },
            ),
            logical_document=document.model_copy(deep=True),
            artifact=Artifact(
                id=artifact_id,
                revision_id=revision_id,
                file_name=view.artifact.file_name,
                extension=view.artifact.extension,
                media_type=view.artifact.media_type,
                byte_size=0,
                sha256=tombstone_hash,
                source_path=None,
                cas_uri=None,
                representation_role=view.artifact.representation_role,
                metadata={"tombstone": True},
            ),
            extraction=ExtractionRun(
                id=extraction_id,
                artifact_id=artifact_id,
                parser_name="filesystem-tombstone",
                parser_version="1.0",
                status="succeeded",
                quality_score=1.0,
                output_hash=tombstone_hash,
                metadata={"operation": "delete"},
            ),
            units=[],
        )
        return self._store.ingest_packet(context, packet)

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

    def _identity(
        self,
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
        current = self._store.current_source_revision(context, object_id)
        if current is not None:
            if current.raw_object_uri == path.as_uri() and current.sha256 == record.sha256 and not current.is_tombstone:
                revision_id = current.id
            else:
                # Bind every changed revision to its URI, including a later
                # content reversion after a root move. Reusing a historical
                # hash-only ID could otherwise revive the old root's row.
                revision_id = stable_id("rev", object_id, record.sha256 + "\x1f" + path.as_uri())
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
                revision_key=(
                    record.sha256
                    if identity.revision_id == stable_id("rev", identity.object_id, record.sha256)
                    else record.sha256 + ":" + identity.revision_id
                ),
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
                representation_role=self._parsers.representation_role(extension),
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
