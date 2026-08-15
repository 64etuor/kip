from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from kip.application.ingestion_events import EventFamily, EventIngestionWorkflow
from kip.application.ingestion_files import FileIngestionWorkflow
from kip.domain.egress import DataClassification
from kip.domain.identity import AclSnapshot
from kip.domain.models import (
    ConnectorEvent,
    IngestResult,
    ReextractionSummary,
    RequestContext,
    SyncSummary,
)
from kip.errors import ConfigurationError, KipError, ValidationError
from kip.ids import stable_id
from kip.ports.evidence import EvidenceStore, SourceFileInspectorPort
from kip.ports.ingestion import (
    ContentAddressedStorePort,
    DiscoveredFile,
    IngestionStore,
    ParserRegistryPort,
    SourceCatalogPort,
)
from kip.ports.jobs import JobStore
from kip.ports.text_analyzer import TextAnalyzerPort

_DEFAULT_REEXTRACTION_EXTENSIONS: frozenset[str] = frozenset({".hwp", ".hwpx"})


class IngestionUseCases:
    def __init__(
        self,
        store: IngestionStore,
        jobs: JobStore,
        sources: SourceCatalogPort,
        parsers: ParserRegistryPort,
        analyzer: TextAnalyzerPort,
        content_store: ContentAddressedStorePort,
        source_files: SourceFileInspectorPort,
        evidence: EvidenceStore,
        *,
        minimum_quality_score: float,
        deletion_grace_scans: int = 2,
    ) -> None:
        self._store = store
        self._jobs = jobs
        self._sources = sources
        self._files = FileIngestionWorkflow(
            store,
            evidence,
            parsers,
            analyzer,
            source_files,
        )
        self._events = EventIngestionWorkflow(store, analyzer, content_store)
        self._minimum_quality_score = minimum_quality_score
        self._deletion_grace_scans = deletion_grace_scans

    def ingest_file(
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
        return self._files.ingest(
            context,
            source_name=source_name,
            source_root=source_root,
            record=record,
            acl_scopes=acl_scopes,
            acl_snapshot=acl_snapshot,
            classification=classification,
        )

    def sync_filesystem(
        self,
        context: RequestContext,
        source_name: str,
        *,
        dry_run: bool = False,
    ) -> SyncSummary:
        source = self._sources.filesystem(source_name)
        scope = source.acl_scope or f"workspace:{context.workspace}"
        summary = SyncSummary(source=source_name)
        system_id = stable_id("srcsys", context.workspace, source_name)
        seen_object_ids: set[str] = set()
        # If source.scan() raises (unavailable mount, walk failure), the
        # exception propagates before any reconciliation: a failed or partial
        # scan never marks absences and never tombstones.
        for record in source.scan():
            summary.scanned += 1
            # A scanned file counts as seen even when its ingest fails: the
            # file exists, so it must never move toward deletion.
            seen_object_ids.add(
                stable_id("srcobj", system_id, record.relative_path)
            )
            if dry_run:
                summary.skipped += 1
                continue
            try:
                result = self.ingest_file(
                    context,
                    source_name=source_name,
                    source_root=source.root,
                    record=record,
                    acl_scopes=[scope],
                    acl_snapshot=source.acl_snapshot,
                    classification=source.classification,
                )
            except (KipError, OSError) as exc:
                summary.failed += 1
                summary.warnings.append(
                    f"{record.relative_path}: {type(exc).__name__}: {exc}"
                )
                continue
            self._record_result(summary, result)
        if not dry_run:
            self._reconcile_filesystem_deletions(
                context,
                summary,
                system_id=system_id,
                scope=scope,
                seen_object_ids=seen_object_ids,
            )
        return summary

    def _reconcile_filesystem_deletions(
        self,
        context: RequestContext,
        summary: SyncSummary,
        *,
        system_id: str,
        scope: str,
        seen_object_ids: set[str],
    ) -> None:
        """Apply the deletion grace policy after a complete, successful scan.

        An object absent from `deletion_grace_scans` consecutive complete
        scans is soft-tombstoned; a reappearing object clears its absence
        mark. An entirely empty scan is treated as a possibly unavailable
        mount and never contributes absence evidence.
        """
        if not seen_object_ids:
            summary.warnings.append(
                "scan saw no files; skipping deletion reconciliation "
                "(a source outage must not be interpreted as deletion)"
            )
            return
        scan_context = context.model_copy(
            update={"acl_scopes": sorted(set(context.acl_scopes).union([scope]))}
        )
        absences = self._store.reconcile_scan_absences(
            scan_context,
            system_id,
            seen_object_ids,
        )
        for absence in absences:
            summary.absent += 1
            if absence.absent_scan_count < self._deletion_grace_scans:
                continue
            try:
                self._files.tombstone_absent(scan_context, absence)
            except (KipError, OSError) as exc:
                summary.failed += 1
                summary.warnings.append(
                    f"{absence.external_id}: {type(exc).__name__}: {exc}"
                )
                continue
            summary.tombstoned += 1

    def reextract_filesystem(
        self,
        context: RequestContext,
        source_name: str,
        *,
        activate: bool = False,
        extensions: frozenset[str] = _DEFAULT_REEXTRACTION_EXTENSIONS,
    ) -> ReextractionSummary:
        source = self._sources.filesystem(source_name)
        scope = source.acl_scope or f"workspace:{context.workspace}"
        summary = ReextractionSummary(source=source_name, activate=activate)
        for record in source.scan(include_extensions=set(extensions)):
            summary.scanned += 1
            if record.path.suffix.lower() not in extensions:
                summary.skipped += 1
                continue
            summary.eligible += 1
            try:
                prepared = self._files.prepare_reextraction(
                    context,
                    source_name=source_name,
                    source_root=source.root,
                    record=record,
                    acl_scopes=[scope],
                )
            except (KipError, OSError) as exc:
                summary.failed += 1
                summary.warnings.append(
                    f"{record.relative_path}: {type(exc).__name__}: {exc}"
                )
                continue
            extraction = prepared.packet.extraction
            summary.parsed += 1
            summary.unit_count += len(prepared.packet.units)
            summary.parser_counts[extraction.parser_name] = (
                summary.parser_counts.get(extraction.parser_name, 0) + 1
            )
            summary.warnings.extend(
                f"{record.relative_path}: {warning}"
                for warning in extraction.warnings
            )
            quality_score = extraction.quality_score or 0.0
            if quality_score < self._minimum_quality_score:
                summary.rejected += 1
                summary.warnings.append(
                    f"{record.relative_path}: quality {quality_score:.3f} is below "
                    f"{self._minimum_quality_score:.3f}"
                )
                continue
            if not activate:
                continue
            try:
                self._files.activate_reextraction(prepared)
            except (KipError, OSError) as exc:
                summary.failed += 1
                summary.warnings.append(
                    f"{record.relative_path}: {type(exc).__name__}: {exc}"
                )
                continue
            summary.activated += 1
        return summary

    def sync_remote(
        self,
        context: RequestContext,
        source_name: str,
        *,
        since: str | None = None,
    ) -> SyncSummary:
        return self._sync_events(
            context,
            source_name,
            self._sources.events(source_name, since=since),
        )

    def _sync_events(
        self,
        context: RequestContext,
        source_name: str,
        events: Iterable[ConnectorEvent],
    ) -> SyncSummary:
        summary = SyncSummary(source=source_name)
        for event in events:
            summary.scanned += 1
            try:
                result = self.ingest_connector_event(context, event)
            except (KipError, OSError) as exc:
                summary.failed += 1
                summary.warnings.append(
                    f"{event.external_id}: {type(exc).__name__}: {exc}"
                )
                continue
            self._record_result(summary, result)
        return summary

    def ingest_connector_event(
        self,
        context: RequestContext,
        event: ConnectorEvent,
    ) -> IngestResult:
        selected = event.model_copy(
            update={"acl_snapshot": self._sources.event_acl_snapshot(event)}
        )
        return self._events.ingest(
            context,
            selected,
            classification=self._sources.event_classification(event),
            family=self._resolve_event_family(event.connector_name),
        )

    def _resolve_event_family(self, connector_name: str) -> EventFamily:
        declared = self._sources.event_family(connector_name)
        try:
            return EventFamily(declared)
        except ValueError as exc:
            raise ConfigurationError(
                f"connector event family is invalid: {connector_name} -> {declared}"
            ) from exc

    @staticmethod
    def _record_result(summary: SyncSummary, result: IngestResult) -> None:
        match result.status:
            case "inserted":
                summary.inserted += 1
            case "replaced":
                summary.replaced += 1
            case "unchanged":
                summary.unchanged += 1
            case "failed":
                summary.failed += 1
        summary.warnings.extend(result.warnings)

    def enqueue_sync(self, context: RequestContext, source_name: str) -> str:
        enabled = self.enabled_sync_sources()
        if source_name not in enabled:
            raise ValidationError(
                f"source is not enabled: {source_name}; "
                f"enabled sources: {', '.join(enabled) or 'none'}"
            )
        return self._jobs.enqueue_job(
            context,
            "sync.source",
            {"source_name": source_name, "workspace": context.workspace},
            idempotency_key=f"sync:{context.workspace}:{source_name}",
        )

    def enabled_sync_sources(self) -> list[str]:
        return self._sources.enabled_names()
