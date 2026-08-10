from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from kip.application.ingestion_events import EventIngestionWorkflow
from kip.application.ingestion_files import FileIngestionWorkflow
from kip.domain.egress import DataClassification
from kip.domain.identity import AclSnapshot
from kip.domain.models import ConnectorEvent, IngestResult, RequestContext, SyncSummary
from kip.errors import KipError, ValidationError
from kip.ports.ingestion import (
    ContentAddressedStorePort,
    DiscoveredFile,
    IngestionStore,
    ParserRegistryPort,
    SourceCatalogPort,
)
from kip.ports.jobs import JobStore
from kip.ports.text_analyzer import TextAnalyzerPort


class IngestionUseCases:
    def __init__(
        self,
        store: IngestionStore,
        jobs: JobStore,
        sources: SourceCatalogPort,
        parsers: ParserRegistryPort,
        analyzer: TextAnalyzerPort,
        content_store: ContentAddressedStorePort,
    ) -> None:
        self._jobs = jobs
        self._sources = sources
        self._files = FileIngestionWorkflow(store, parsers, analyzer)
        self._events = EventIngestionWorkflow(store, analyzer, content_store)

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
        for record in source.scan():
            summary.scanned += 1
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
        return summary

    def sync_slack(
        self,
        context: RequestContext,
        *,
        oldest: str | None = None,
    ) -> SyncSummary:
        return self._sync_events(
            context,
            "slack",
            self._sources.events("slack", since=oldest),
        )

    def sync_imap(self, context: RequestContext) -> SyncSummary:
        return self._sync_events(context, "imap", self._sources.events("imap"))

    def sync_apple_mail(self, context: RequestContext) -> SyncSummary:
        return self._sync_events(
            context,
            "apple-mail",
            self._sources.events("apple-mail"),
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
        )

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
