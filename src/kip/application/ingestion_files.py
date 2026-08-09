from __future__ import annotations

import mimetypes
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from kip.application.analyzer import KoreanNgramAnalyzer
from kip.domain.models import (
    Artifact,
    DocumentPacket,
    IngestResult,
    LogicalDocument,
    RequestContext,
    SourceObject,
    SourceRevision,
)
from kip.errors import ValidationError
from kip.ids import stable_id
from kip.ports.ingestion import DiscoveredFile, IngestionStore, ParserRegistryPort

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


class FileIngestionWorkflow:
    def __init__(
        self,
        store: IngestionStore,
        parsers: ParserRegistryPort,
        analyzer: KoreanNgramAnalyzer,
    ) -> None:
        self._store = store
        self._parsers = parsers
        self._analyzer = analyzer

    def ingest(
        self,
        context: RequestContext,
        *,
        source_name: str,
        source_root: Path,
        record: DiscoveredFile,
        acl_scopes: list[str],
    ) -> IngestResult:
        path = _safe_source_path(record.path, source_root)
        system_id = stable_id("srcsys", context.workspace, source_name)
        object_id = stable_id("srcobj", system_id, record.relative_path)
        revision_id = stable_id("rev", object_id, record.sha256)
        stable_key = _logical_key(record.relative_path)
        document_id = stable_id("ldoc", context.workspace, stable_key)
        artifact_id = stable_id("art", revision_id, path.name)
        if self._store.has_revision(context, object_id, record.sha256):
            return IngestResult(
                status="unchanged",
                source_object_id=object_id,
                revision_id=revision_id,
                artifact_id=artifact_id,
                document_id=document_id,
                unit_count=0,
            )

        source_modified_at = datetime.fromtimestamp(
            record.mtime_ns / 1_000_000_000,
            tz=UTC,
        )
        extension = path.suffix.lower()
        extraction, units = self._parsers.find(path).parse(
            path,
            artifact_id=artifact_id,
            document_id=document_id,
            acl_scopes=acl_scopes,
        )
        for unit in units:
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
            workspace_id=context.workspace,
            source_object=SourceObject(
                id=object_id,
                system_id=system_id,
                system_name=source_name,
                system_kind="filesystem",
                external_id=record.relative_path,
                object_type="file",
                canonical_uri=path.as_uri(),
                acl_scopes=acl_scopes,
                metadata={"relative_path": record.relative_path},
            ),
            revision=SourceRevision(
                id=revision_id,
                object_id=object_id,
                revision_key=record.sha256,
                sha256=record.sha256,
                size_bytes=record.size,
                source_modified_at=source_modified_at,
                raw_object_uri=path.as_uri(),
                metadata={"mtime_ns": record.mtime_ns},
            ),
            logical_document=LogicalDocument(
                id=document_id,
                stable_key=stable_key,
                title=path.stem,
                metadata={
                    "source_name": source_name,
                    "relative_path": record.relative_path,
                },
            ),
            artifact=Artifact(
                id=artifact_id,
                revision_id=revision_id,
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
        ingest_context = context.model_copy(
            update={
                "acl_scopes": sorted(set(context.acl_scopes).union(acl_scopes))
            }
        )
        return self._store.ingest_packet(ingest_context, packet)
