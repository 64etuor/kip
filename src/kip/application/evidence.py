from __future__ import annotations

from pathlib import Path

from kip.domain.json_types import JsonObject
from kip.domain.models import ArtifactView, EvidenceRead, RequestContext, XlsxRangeRead
from kip.errors import ConflictError, NotFoundError, ValidationError
from kip.ports.evidence import EvidenceStore, SourceFileInspectorPort, WorkbookReaderPort


class EvidenceUseCases:
    def __init__(
        self,
        store: EvidenceStore,
        source_files: SourceFileInspectorPort,
        workbooks: WorkbookReaderPort,
    ) -> None:
        self._store = store
        self._source_files = source_files
        self._workbooks = workbooks

    def read_unit(
        self,
        context: RequestContext,
        unit_id: str,
        *,
        verify_hash: bool = True,
    ) -> EvidenceRead:
        unit = self._store.get_content_unit(context, unit_id)
        view = self._store.get_artifact(context, unit.artifact_id)
        if not view.source_object or not view.revision:
            raise NotFoundError(f"source metadata missing for unit: {unit_id}")
        # verify_hash=False lets bulk reopen paths (context bundles, answer
        # evidence) reuse the sync trust model: an unchanged (size, mtime_ns)
        # stat counts as unchanged content without re-reading the file. Any
        # mismatch or missing stat metadata falls back to the full hash.
        if not verify_hash and self._stat_matches_revision(view):
            return EvidenceRead(
                unit=unit,
                source_uri=view.source_object.canonical_uri,
                indexed_source_sha256=view.revision.sha256,
                current_source_sha256=view.revision.sha256,
                source_changed_since_index=False,
            )
        current_hash = self._current_hash(view.artifact.source_path)
        return EvidenceRead(
            unit=unit,
            source_uri=view.source_object.canonical_uri,
            indexed_source_sha256=view.revision.sha256,
            current_source_sha256=current_hash,
            source_changed_since_index=current_hash != view.revision.sha256,
        )

    def _stat_matches_revision(self, view: ArtifactView) -> bool:
        revision = view.revision
        source_path = view.artifact.source_path
        if revision is None or not source_path:
            return False
        recorded_mtime = revision.metadata.get("mtime_ns")
        if revision.size_bytes is None or not isinstance(recorded_mtime, int):
            return False
        current = self._source_files.stat(Path(source_path))
        return current == (revision.size_bytes, recorded_mtime)

    def read_xlsx(
        self,
        context: RequestContext,
        artifact_id: str,
        *,
        sheet: str,
        cell_range: str,
        require_fresh: bool = True,
    ) -> XlsxRangeRead:
        view = self._store.get_artifact(context, artifact_id)
        path_value = view.artifact.source_path
        if not path_value:
            raise ValidationError("artifact has no live source path")
        path = Path(path_value).resolve()
        if not self._workbooks.supports(path):
            raise ValidationError("artifact is not an XLSX/XLSM workbook")
        current_hash = self._source_files.require_sha256(path)
        if require_fresh and current_hash != view.artifact.sha256:
            raise ConflictError(
                "source workbook changed since indexing; re-index before reading"
            )
        cells = self._workbooks.read(path, sheet, cell_range)
        source_uri = (
            view.source_object.canonical_uri if view.source_object else path.as_uri()
        )
        return XlsxRangeRead(
            artifact_id=artifact_id,
            source_uri=source_uri,
            sheet=sheet,
            cell_range=cell_range,
            cells=cells,
            indexed_source_sha256=view.artifact.sha256,
            current_source_sha256=current_hash,
            source_changed_since_index=current_hash != view.artifact.sha256,
        )

    def get_artifact(
        self,
        context: RequestContext,
        artifact_id: str,
    ) -> ArtifactView:
        return self._store.get_artifact(context, artifact_id)

    def get_document(
        self,
        context: RequestContext,
        document_id: str,
    ) -> JsonObject:
        return self._store.get_document(context, document_id)

    def _current_hash(self, source_path: str | None) -> str | None:
        if not source_path:
            return None
        return self._source_files.sha256(Path(source_path))
