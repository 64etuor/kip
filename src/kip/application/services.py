from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kip.adapters.connectors.apple_mail import AppleMailConnector
from kip.adapters.connectors.filesystem import FileRecord, FileSystemConnector
from kip.adapters.connectors.imap import ImapConnector
from kip.adapters.connectors.slack import SlackConnector
from kip.adapters.parsers.registry import ParserRegistry
from kip.adapters.parsers.xlsx import read_xlsx_range
from kip.application.analyzer import KoreanNgramAnalyzer, normalize_text
from kip.application.retrieval import apply_rerank, reciprocal_rank_fusion
from kip.domain.models import (
    Artifact,
    AssertionCandidate,
    AssertionExplanation,
    Capabilities,
    ConnectorEvent,
    ContentUnit,
    ContextBundle,
    ContextItem,
    ContextRequest,
    DocumentPacket,
    EmbeddingRecord,
    EmbeddingSpace,
    EvidenceLocator,
    EvidenceRead,
    ExtractionRun,
    IngestResult,
    LogicalDocument,
    RequestContext,
    SearchRequest,
    SourceObject,
    SourceRevision,
    SyncSummary,
    XlsxRangeRead,
)
from kip.errors import (
    ConfigurationError,
    ConflictError,
    DependencyUnavailableError,
    NotFoundError,
    ValidationError,
)
from kip.ids import new_id, sha256_bytes, stable_id
from kip.ports.embedding import EmbeddingPort
from kip.ports.repository import RepositoryPort
from kip.ports.reranker import RerankerPort
from kip.settings import Settings

_VERSION_SUFFIX_RE = re.compile(
    r"(?:[ _.-]*(?:검색본|열람본|pdf|scan|scanned|원본파일))$", re.IGNORECASE
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
        ".xls": "workbook",
    }.get(extension.lower(), "primary")


class KnowledgeService:
    """Application layer shared by CLI, REST, MCP, and worker adapters."""

    def __init__(
        self,
        settings: Settings,
        repository: RepositoryPort,
        parsers: ParserRegistry,
        analyzer: KoreanNgramAnalyzer,
        embedding: EmbeddingPort,
        reranker: RerankerPort | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.parsers = parsers
        self.analyzer = analyzer
        self.embedding = embedding
        self.reranker = reranker
        self.settings.cas_path.mkdir(parents=True, exist_ok=True)

    def request_context(
        self,
        *,
        workspace: str | None = None,
        principal_id: str = "principal_local",
        acl_scopes: list[str] | None = None,
        request_id: str | None = None,
    ) -> RequestContext:
        selected_workspace = workspace or self.settings.workspace
        return RequestContext(
            workspace=selected_workspace,
            principal_id=principal_id,
            acl_scopes=acl_scopes or [f"workspace:{selected_workspace}"],
            request_id=request_id or new_id("req"),
        )

    def capabilities(self) -> Capabilities:
        connectors = {
            "filesystem": "configured" if self.settings.get("sources.filesystem", []) else "disabled",
            "slack": "configured" if self.settings.get("sources.slack.enabled", False) else "disabled",
            "apple_mail": "configured" if self.settings.get("sources.apple_mail.enabled", False) else "disabled",
            "imap": "configured" if self.settings.get("sources.imap.enabled", False) else "disabled",
        }
        warnings: list[str] = []
        if self.settings.database_url.startswith("memory://"):
            warnings.append("memory repository is non-durable and intended only for tests or demos")
        semantic_enabled = bool(self.settings.get("search.semantic_enabled", False))
        if semantic_enabled and self.embedding.name == "disabled":
            warnings.append("semantic search is enabled but no embedding adapter is configured")
        return Capabilities(
            repository=self.repository.name,
            lexical_search=True,
            semantic_search=semantic_enabled and self.embedding.name != "disabled",
            graph_backend=str(self.settings.get("graph.backend", "postgres")),
            api=True,
            mcp=True,
            parsers=self.parsers.capabilities(),
            connectors=connectors,
            warnings=warnings,
        )

    def migrate(self) -> list[str]:
        return self.repository.migrate(self.settings.project_root / "migrations")

    def ingest_file(
        self,
        context: RequestContext,
        *,
        source_name: str,
        source_root: Path,
        record: FileRecord,
        acl_scopes: list[str],
    ) -> IngestResult:
        path = _safe_source_path(record.path, source_root)
        system_id = stable_id("srcsys", context.workspace, source_name)
        object_id = stable_id("srcobj", system_id, record.relative_path)
        if self.repository.has_revision(context, object_id, record.sha256):
            return IngestResult(
                status="unchanged",
                source_object_id=object_id,
                revision_id=stable_id("rev", object_id, record.sha256),
                artifact_id=stable_id("art", stable_id("rev", object_id, record.sha256), path.name),
                document_id=stable_id("ldoc", context.workspace, _logical_key(record.relative_path)),
                unit_count=0,
            )

        revision_id = stable_id("rev", object_id, record.sha256)
        stable_key = _logical_key(record.relative_path)
        document_id = stable_id("ldoc", context.workspace, stable_key)
        artifact_id = stable_id("art", revision_id, path.name)
        source_modified_at = datetime.fromtimestamp(record.mtime_ns / 1_000_000_000, tz=UTC)
        extension = path.suffix.lower()
        parser = self.parsers.find(path)
        extraction, units = parser.parse(
            path,
            artifact_id=artifact_id,
            document_id=document_id,
            acl_scopes=acl_scopes,
        )
        for unit in units:
            unit.lexical_text = self.analyzer.analyze(
                "\n".join(
                    [
                        unit.title or "",
                        unit.body_normalized,
                        path.name,
                        record.relative_path,
                    ]
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
                metadata={"source_name": source_name, "relative_path": record.relative_path},
            ),
            artifact=Artifact(
                id=artifact_id,
                revision_id=revision_id,
                file_name=path.name,
                extension=extension,
                media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
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
            update={"acl_scopes": sorted(set(context.acl_scopes).union(acl_scopes))}
        )
        return self.repository.ingest_packet(ingest_context, packet)

    def sync_filesystem(
        self,
        context: RequestContext,
        source_name: str,
        *,
        dry_run: bool = False,
    ) -> SyncSummary:
        source = self.settings.filesystem_source(source_name)
        if not source or not source.get("enabled", True):
            raise ConfigurationError(f"filesystem source is missing or disabled: {source_name}")
        root_value = Path(str(source.get("root", "")))
        root = root_value if root_value.is_absolute() else (self.settings.project_root / root_value)
        include_extensions = {str(item).lower() for item in source.get("include_extensions", [])}
        scope = str(source.get("acl_scope") or f"workspace:{context.workspace}")
        connector = FileSystemConnector(
            root,
            include_extensions=include_extensions,
            exclude_globs=[str(item) for item in source.get("exclude_globs", [])],
            settle_seconds=float(source.get("settle_seconds", 2)),
            follow_symlinks=bool(self.settings.get("security.follow_symlinks", False)),
            max_file_bytes=int(self.settings.get("security.max_file_bytes", 500 * 1024 * 1024)),
        )
        summary = SyncSummary(source=source_name)
        for record in connector.scan():
            summary.scanned += 1
            if dry_run:
                summary.skipped += 1
                continue
            try:
                result = self.ingest_file(
                    context,
                    source_name=source_name,
                    source_root=root,
                    record=record,
                    acl_scopes=[scope],
                )
                if result.status == "inserted":
                    summary.inserted += 1
                elif result.status == "replaced":
                    summary.replaced += 1
                elif result.status == "unchanged":
                    summary.unchanged += 1
                summary.warnings.extend(result.warnings)
            except Exception as exc:
                summary.failed += 1
                summary.warnings.append(f"{record.relative_path}: {type(exc).__name__}: {exc}")
        return summary

    def sync_slack(self, context: RequestContext, *, oldest: str | None = None) -> SyncSummary:
        config = self.settings.get("sources.slack", {}) or {}
        if not config.get("enabled", False):
            raise ConfigurationError("Slack connector is disabled")
        connector = SlackConnector(
            workspace_id=str(config.get("workspace_id", "")),
            allowed_conversation_ids=[str(item) for item in config.get("allowed_conversation_ids", [])],
            token_env=str(config.get("token_env", "KIP_SLACK_BOT_TOKEN")),
        )
        return self._sync_events(context, "slack", connector.pull_messages(oldest=oldest))

    def sync_imap(self, context: RequestContext) -> SyncSummary:
        config = self.settings.get("sources.imap", {}) or {}
        if not config.get("enabled", False):
            raise ConfigurationError("IMAP connector is disabled")
        connector = ImapConnector(
            host=str(config.get("host", "")),
            port=int(config.get("port", 993)),
            mailboxes=[str(item) for item in config.get("mailboxes", [])],
            username_env=str(config.get("username_env", "KIP_IMAP_USERNAME")),
            password_env=str(config.get("password_env", "KIP_IMAP_PASSWORD")),
            use_ssl=bool(config.get("use_ssl", True)),
        )
        return self._sync_events(context, "imap", connector.pull())

    def sync_apple_mail(self, context: RequestContext) -> SyncSummary:
        config = self.settings.get("sources.apple_mail", {}) or {}
        if not config.get("enabled", False):
            raise ConfigurationError("Apple Mail connector is disabled")
        connector = AppleMailConnector(
            script_path=self.settings.project_root / "scripts/apple_mail_export.jxa",
            allowed_accounts=[str(item) for item in config.get("allowed_accounts", [])],
            allowed_mailboxes=[str(item) for item in config.get("allowed_mailboxes", [])],
            lookback_days=int(config.get("lookback_days", 30)),
            limit_per_mailbox=int(config.get("limit_per_mailbox", 500)),
        )
        return self._sync_events(context, "apple-mail", connector.pull())

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
                if result.status == "inserted":
                    summary.inserted += 1
                elif result.status == "replaced":
                    summary.replaced += 1
                elif result.status == "unchanged":
                    summary.unchanged += 1
            except Exception as exc:
                summary.failed += 1
                summary.warnings.append(f"{event.external_id}: {type(exc).__name__}: {exc}")
        return summary

    def ingest_connector_event(self, context: RequestContext, event: ConnectorEvent) -> IngestResult:
        payload_bytes = json.dumps(event.payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        payload_hash = sha256_bytes(payload_bytes)
        revision_hash = sha256_bytes(payload_bytes + b"\0" + event.operation.encode("utf-8"))
        system_kind = str(event.payload.get("source_kind") or (
            "slack" if event.connector_name == "slack" else
            "mail" if event.connector_name in {"imap", "apple-mail"} else
            "connector"
        ))
        system_id = stable_id("srcsys", context.workspace, event.connector_name)
        object_id = stable_id("srcobj", system_id, event.external_id)
        revision_id = stable_id("rev", object_id, revision_hash)
        artifact_id = stable_id("art", revision_id, "payload.json")
        document_id = stable_id("ldoc", context.workspace, f"{event.connector_name}:{event.external_id}")
        if self.repository.has_revision(context, object_id, revision_hash):
            return IngestResult(
                status="unchanged",
                source_object_id=object_id,
                revision_id=revision_id,
                artifact_id=artifact_id,
                document_id=document_id,
            )

        cas_uri = self._put_cas(payload_bytes, suffix=".json")
        title, body, locator = self._event_content(event)
        extraction_id = new_id("ext")
        units: list[ContentUnit] = []
        if event.operation != "delete":
            normalized = normalize_text(body)
            units.append(
                ContentUnit(
                    id=stable_id("unit", extraction_id, "0"),
                    extraction_id=extraction_id,
                    document_id=document_id,
                    artifact_id=artifact_id,
                    ordinal=0,
                    unit_type="slack_message" if system_kind == "slack" else "email_message",
                    title=title,
                    body=body,
                    body_normalized=normalized,
                    lexical_text=self.analyzer.analyze(f"{title}\n{normalized}\n{event.external_id}"),
                    locator=locator,
                    acl_scopes=list(event.acl_scopes),
                    metadata={"connector": event.connector_name},
                )
            )
        extraction = ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name=f"{event.connector_name}-normalizer",
            parser_version="1.0",
            status="succeeded",
            quality_score=1.0,
            output_hash=sha256_bytes(body.encode("utf-8")),
            metadata={"operation": event.operation},
        )
        canonical_uri = self._event_uri(event)
        packet = DocumentPacket(
            workspace_id=context.workspace,
            source_object=SourceObject(
                id=object_id,
                system_id=system_id,
                system_name=event.connector_name,
                system_kind=system_kind,
                external_id=event.external_id,
                object_type="message",
                canonical_uri=canonical_uri,
                acl_scopes=list(event.acl_scopes),
                metadata={"connector_event_id": event.event_id},
            ),
            revision=SourceRevision(
                id=revision_id,
                object_id=object_id,
                revision_key=revision_hash,
                sha256=revision_hash,
                size_bytes=len(payload_bytes),
                source_modified_at=event.occurred_at,
                raw_object_uri=cas_uri,
                is_tombstone=event.operation == "delete",
                metadata={"event_operation": event.operation},
            ),
            logical_document=LogicalDocument(
                id=document_id,
                stable_key=f"{event.connector_name}:{event.external_id}",
                title=title,
                document_type="communication",
                metadata={"connector": event.connector_name},
            ),
            artifact=Artifact(
                id=artifact_id,
                revision_id=revision_id,
                file_name="payload.json",
                extension=".json",
                media_type="application/json",
                byte_size=len(payload_bytes),
                sha256=payload_hash,
                cas_uri=cas_uri,
                representation_role="source_snapshot",
                metadata={},
            ),
            extraction=extraction,
            units=units,
        )
        ingest_context = context.model_copy(
            update={"acl_scopes": sorted(set(context.acl_scopes).union(event.acl_scopes))}
        )
        return self.repository.ingest_packet(ingest_context, packet)

    @staticmethod
    def _event_content(event: ConnectorEvent) -> tuple[str, str, EvidenceLocator]:
        payload = event.payload
        if event.connector_name == "slack":
            title = f"Slack {payload.get('conversation_id', '')} {payload.get('ts', '')}"
            body = str(payload.get("text", ""))
            locator = EvidenceLocator(
                type="slack_message",
                data={
                    "workspace_id": payload.get("workspace_id"),
                    "conversation_id": payload.get("conversation_id"),
                    "ts": payload.get("ts"),
                    "thread_ts": payload.get("thread_ts"),
                },
            )
            return title, body, locator
        title = str(payload.get("subject") or payload.get("title") or "Connector message")
        body = str(payload.get("text") or payload.get("content") or payload.get("body") or "")
        if event.connector_name in {"imap", "apple-mail"}:
            locator = EvidenceLocator(
                type="email_message",
                data={
                    "account_id": payload.get("account_id") or payload.get("account"),
                    "mailbox": payload.get("mailbox"),
                    "message_id": payload.get("message_id"),
                    "uid": payload.get("uid") or payload.get("mail_internal_id"),
                },
            )
        else:
            locator = EvidenceLocator(
                type="connector_object",
                data={"connector": event.connector_name, "external_id": event.external_id},
            )
        return title, body, locator

    @staticmethod
    def _event_uri(event: ConnectorEvent) -> str:
        if event.connector_name == "slack":
            payload = event.payload
            return "slack://{}/{}/{}".format(
                payload.get("workspace_id", ""),
                payload.get("conversation_id", ""),
                payload.get("ts", ""),
            )
        message_id = str(event.payload.get("message_id") or event.external_id)
        if event.connector_name in {"imap", "apple-mail"}:
            return f"mail://{message_id}"
        return f"connector://{event.connector_name}/{event.external_id}"

    def _put_cas(self, data: bytes, *, suffix: str = "") -> str:
        digest = sha256_bytes(data)
        target = self.settings.cas_path / "sha256" / digest[:2] / f"{digest}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temp = target.with_suffix(target.suffix + ".tmp")
            temp.write_bytes(data)
            os.replace(temp, target)
        return target.as_uri()

    def embedding_space(self, context: RequestContext) -> EmbeddingSpace:
        configured = dict(self.settings.get("models.embedding", {}) or {})
        space_name = str(
            configured.get("space_name")
            or f"{self.embedding.model}-{self.embedding.revision}-{self.embedding.dimensions}"
        )
        configuration = {"space_name": space_name}
        if configured.get("document_instruction"):
            configuration["document_instruction"] = str(configured["document_instruction"])
        space_id = stable_id(
            "espace",
            context.workspace,
            "\0".join(
                [
                    self.embedding.provider,
                    self.embedding.model,
                    self.embedding.revision,
                    str(self.embedding.dimensions),
                    str(self.embedding.normalized),
                    json.dumps(
                        configuration,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                ]
            ),
        )
        return EmbeddingSpace(
            id=space_id,
            name=space_name,
            provider=self.embedding.provider,
            model=self.embedding.model,
            revision=self.embedding.revision,
            dimensions=self.embedding.dimensions,
            normalized=self.embedding.normalized,
            status="shadow",
            configuration=configuration,
        )

    def rebuild_semantic_projection(self, context: RequestContext) -> dict[str, Any]:
        if self.embedding.name == "disabled":
            raise ConfigurationError("no embedding adapter is configured")
        space = self.repository.save_embedding_space(context, self.embedding_space(context))
        units = self.repository.list_embeddable_units(context)
        batch_size = int(self.settings.get("models.embedding.batch_size", 16))
        indexed = 0
        for offset in range(0, len(units), batch_size):
            batch = units[offset : offset + batch_size]
            texts = [
                "\n".join(part for part in (unit.title, unit.body_normalized) if part)
                for unit in batch
            ]
            embeddings = self.embedding.embed_documents(texts)
            if len(embeddings) != len(batch):
                raise DependencyUnavailableError(
                    "embedding response count does not match semantic rebuild batch"
                )
            indexed += self.repository.upsert_embeddings(
                context,
                space.id,
                [
                    EmbeddingRecord(
                        unit_id=unit.unit_id,
                        embedding=embedding,
                        source_hash=unit.source_hash,
                    )
                    for unit, embedding in zip(batch, embeddings, strict=True)
                ],
            )
        return {
            "projection": "semantic",
            "status": "shadow",
            "space_id": space.id,
            "space_name": space.name,
            "model": space.model,
            "revision": space.revision,
            "dimensions": space.dimensions,
            "indexed_units": indexed,
            "content_units": len(units),
            "in_sync": indexed == len(units),
        }

    def activate_semantic_projection(
        self,
        context: RequestContext,
        space_id: str | None = None,
    ) -> EmbeddingSpace:
        selected = space_id or self.embedding_space(context).id
        verification = self.verify_semantic_projection(context, space_id=selected)
        if not verification["ok"]:
            raise ConflictError(
                "semantic projection cannot be activated until every active content unit is indexed"
            )
        return self.repository.activate_embedding_space(context, selected)

    def verify_semantic_projection(
        self,
        context: RequestContext,
        *,
        space_id: str | None = None,
    ) -> dict[str, Any]:
        if self.embedding.name == "disabled" and space_id is None:
            return {
                "projection": "semantic",
                "ok": False,
                "status": "disabled",
                "space_id": None,
                "indexed_units": 0,
                "content_units": self.repository.status(context).content_units,
            }
        selected = space_id or self.embedding_space(context).id
        canonical = self.repository.status(context)
        semantic = self.repository.semantic_status(context)
        indexed = int(semantic.get("space_vectors", {}).get(selected, 0))
        status = semantic.get("space_status", {}).get(selected, "missing")
        return {
            "projection": "semantic",
            "ok": status in {"shadow", "active"} and indexed == canonical.content_units,
            "status": status,
            "space_id": selected,
            "indexed_units": indexed,
            "content_units": canonical.content_units,
            "in_sync": indexed == canonical.content_units,
            "active": status == "active",
        }

    @staticmethod
    def _annotate_lexical(hits):
        return [
            hit.model_copy(
                update={
                    "metadata": {
                        **hit.metadata,
                        "retrieval_channels": ["lexical"],
                        "lexical_rank": rank,
                    }
                },
                deep=True,
            )
            for rank, hit in enumerate(hits, start=1)
        ]

    def _semantic_space(
        self,
        context: RequestContext,
        *,
        explicit: bool,
    ) -> EmbeddingSpace:
        if self.embedding.name == "disabled":
            raise DependencyUnavailableError("embedding adapter is disabled")
        if explicit:
            return self.embedding_space(context)
        active = self.repository.active_embedding_space(context)
        if not active:
            raise DependencyUnavailableError("no active embedding space")
        expected = self.embedding_space(context)
        if (
            active.id != expected.id
            or active.model != self.embedding.model
            or active.revision != self.embedding.revision
            or active.dimensions != self.embedding.dimensions
        ):
            raise DependencyUnavailableError(
                "active embedding space does not match the configured embedding adapter"
            )
        return active

    def search(
        self,
        context: RequestContext,
        request: SearchRequest,
        *,
        mode: str | None = None,
    ):
        explicit = mode is not None
        selected_mode = mode or (
            str(self.settings.get("search.default_mode", "reranked"))
            if self.settings.get("search.semantic_enabled", False)
            else "lexical"
        )
        if selected_mode not in {"lexical", "vector", "hybrid", "reranked"}:
            raise ValidationError(f"unsupported search mode: {selected_mode}")
        lexemes = self.analyzer.analyze(request.query)
        if selected_mode == "lexical":
            return self._annotate_lexical(
                self.repository.search(context, request, lexemes)
            )

        candidate_limit = min(
            100,
            max(
                request.limit,
                int(self.settings.get("search.hybrid_candidate_limit", 40)),
            ),
        )
        candidate_request = request.model_copy(update={"limit": candidate_limit})
        lexical = self._annotate_lexical(
            self.repository.search(context, candidate_request, lexemes)
        )
        try:
            space = self._semantic_space(context, explicit=explicit)
            query_embedding = self.embedding.embed_query(request.query)
            vector = self.repository.vector_search(
                context,
                candidate_request,
                query_embedding,
                space_id=space.id,
                limit=candidate_limit,
            )
            if selected_mode == "vector":
                return vector[: request.limit]
            fused = reciprocal_rank_fusion(
                lexical,
                vector,
                limit=candidate_limit,
                rank_constant=int(self.settings.get("search.rrf_rank_constant", 60)),
            )
            if selected_mode == "hybrid":
                return fused[: request.limit]
            if self.reranker is None:
                raise DependencyUnavailableError("reranker adapter is disabled")
            rerank_depth = min(
                len(fused),
                int(self.settings.get("search.rerank_candidate_limit", 20)),
            )
            rerank_hits = fused[:rerank_depth]
            documents = [
                "\n".join(
                    part
                    for part in (
                        hit.title,
                        self.repository.get_content_unit(context, hit.unit_id).body,
                    )
                    if part
                )
                for hit in rerank_hits
            ]
            scores = self.reranker.rerank(request.query, documents)
            return apply_rerank(rerank_hits, scores, limit=request.limit)
        except DependencyUnavailableError:
            if explicit:
                raise
            return [
                hit.model_copy(
                    update={
                        "metadata": {
                            **hit.metadata,
                            "semantic_degraded": True,
                        }
                    },
                    deep=True,
                )
                for hit in lexical[: request.limit]
            ]

    def vocabulary(self, context: RequestContext, prefix: str, limit: int = 20):
        return self.repository.vocabulary(context, prefix, limit)

    def context_bundle(self, context: RequestContext, request: ContextRequest) -> ContextBundle:
        hits = self.search(context, request)
        items: list[ContextItem] = []
        total_chars = 0
        truncated = False
        for hit in hits:
            unit = self.repository.get_content_unit(context, hit.unit_id)
            remaining = request.max_chars - total_chars
            if remaining <= 0:
                truncated = True
                break
            body = unit.body
            if len(body) > remaining:
                body = body[:remaining]
                truncated = True
            view = self.repository.get_artifact(context, unit.artifact_id)
            current_hash = self._current_artifact_hash(view.artifact.source_path)
            items.append(
                ContextItem(
                    hit=hit,
                    body=body,
                    current_source_sha256=current_hash,
                    source_changed_since_index=(
                        current_hash is not None and current_hash != hit.source_sha256
                    ),
                )
            )
            total_chars += len(body)
        return ContextBundle(
            query=request.query,
            items=items,
            total_chars=total_chars,
            truncated=truncated,
        )

    def read_unit(self, context: RequestContext, unit_id: str) -> EvidenceRead:
        unit = self.repository.get_content_unit(context, unit_id)
        view = self.repository.get_artifact(context, unit.artifact_id)
        if not view.source_object or not view.revision:
            raise NotFoundError(f"source metadata missing for unit: {unit_id}")
        current_hash = self._current_artifact_hash(view.artifact.source_path)
        return EvidenceRead(
            unit=unit,
            source_uri=view.source_object.canonical_uri,
            indexed_source_sha256=view.revision.sha256,
            current_source_sha256=current_hash,
            source_changed_since_index=(
                current_hash is not None and current_hash != view.revision.sha256
            ),
        )

    def read_xlsx(
        self,
        context: RequestContext,
        artifact_id: str,
        *,
        sheet: str,
        cell_range: str,
        require_fresh: bool = True,
    ) -> XlsxRangeRead:
        view = self.repository.get_artifact(context, artifact_id)
        path_value = view.artifact.source_path
        if not path_value:
            raise ValidationError("artifact has no live source path")
        path = Path(path_value).resolve()
        if path.suffix.lower() != ".xlsx":
            raise ValidationError("artifact is not an XLSX workbook")
        if not path.exists():
            raise NotFoundError(f"source workbook is unavailable: {path}")
        current_hash = _sha256_file(path)
        if require_fresh and current_hash != view.artifact.sha256:
            raise ConflictError("source workbook changed since indexing; re-index before reading")
        result = read_xlsx_range(path, sheet, cell_range)
        source_uri = view.source_object.canonical_uri if view.source_object else path.as_uri()
        return XlsxRangeRead(
            artifact_id=artifact_id,
            source_uri=source_uri,
            sheet=sheet,
            cell_range=cell_range,
            cells=result["cells"],
            indexed_source_sha256=view.artifact.sha256,
            current_source_sha256=current_hash,
            source_changed_since_index=current_hash != view.artifact.sha256,
        )

    @staticmethod
    def _current_artifact_hash(source_path: str | None) -> str | None:
        if not source_path:
            return None
        path = Path(source_path)
        if not path.exists() or not path.is_file():
            return None
        return _sha256_file(path)

    def enqueue_sync(self, context: RequestContext, source_name: str) -> str:
        enabled = self.enabled_sync_sources()
        if source_name not in enabled:
            raise ValidationError(
                f"source is not enabled: {source_name}; enabled sources: {', '.join(enabled) or 'none'}"
            )
        return self.repository.enqueue_job(
            context,
            "sync.source",
            {"source_name": source_name, "workspace": context.workspace},
            idempotency_key=f"sync:{context.workspace}:{source_name}",
        )

    def enabled_sync_sources(self) -> list[str]:
        sources: list[str] = []
        for source in self.settings.get("sources.filesystem", []) or []:
            if isinstance(source, dict) and source.get("enabled", True) and source.get("name"):
                sources.append(str(source["name"]))
        for name, enabled in [
            ("slack", self.settings.get("sources.slack.enabled", False)),
            ("apple-mail", self.settings.get("sources.apple_mail.enabled", False)),
            ("imap", self.settings.get("sources.imap.enabled", False)),
        ]:
            if enabled:
                sources.append(name)
        return sources

    def create_candidate(self, context: RequestContext, candidate: AssertionCandidate) -> AssertionCandidate:
        return self.repository.save_candidate(context, candidate)

    def review_approve(self, context: RequestContext, candidate_id: str, note: str | None = None):
        return self.repository.approve_candidate(context, candidate_id, context.principal_id, note)

    def review_reject(self, context: RequestContext, candidate_id: str, note: str | None = None):
        return self.repository.reject_candidate(context, candidate_id, context.principal_id, note)

    def explain_assertion(self, context: RequestContext, assertion_id: str) -> AssertionExplanation:
        assertion = self.repository.get_assertion(context, assertion_id)
        evidence = [self.read_unit(context, unit_id) for unit_id in assertion.evidence_unit_ids]
        source_candidate = None
        if assertion.source_candidate_id:
            try:
                source_candidate = self.repository.get_candidate(context, assertion.source_candidate_id)
            except NotFoundError:
                source_candidate = None
        return AssertionExplanation(
            assertion=assertion,
            evidence=evidence,
            source_candidate=source_candidate,
        )
