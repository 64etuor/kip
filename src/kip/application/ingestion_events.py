from __future__ import annotations

import json
from enum import StrEnum
from typing import assert_never

from kip.application.analyzer import KoreanNgramAnalyzer, normalize_text
from kip.domain.egress import DataClassification
from kip.domain.models import (
    Artifact,
    ConnectorEvent,
    ContentUnit,
    DocumentPacket,
    EvidenceLocator,
    ExtractionRun,
    IngestResult,
    LogicalDocument,
    RequestContext,
    SourceObject,
    SourceRevision,
)
from kip.errors import ValidationError
from kip.ids import new_id, sha256_bytes, stable_id
from kip.ports.ingestion import ContentAddressedStorePort, IngestionStore


class EventFamily(StrEnum):
    SLACK = "slack"
    MAIL = "mail"
    CONNECTOR = "connector"


_EVENT_FAMILIES = {"slack": EventFamily.SLACK, "imap": EventFamily.MAIL, "apple-mail": EventFamily.MAIL}


class EventIngestionWorkflow:
    def __init__(
        self,
        store: IngestionStore,
        analyzer: KoreanNgramAnalyzer,
        content_store: ContentAddressedStorePort,
    ) -> None:
        self._store = store
        self._analyzer = analyzer
        self._content_store = content_store

    def ingest(
        self,
        context: RequestContext,
        event: ConnectorEvent,
        *,
        classification: DataClassification,
    ) -> IngestResult:
        payload_bytes = json.dumps(
            event.payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        payload_hash = sha256_bytes(payload_bytes)
        revision_hash = sha256_bytes(
            payload_bytes + b"\0" + event.operation.encode("utf-8")
        )
        family = _EVENT_FAMILIES.get(event.connector_name, EventFamily.CONNECTOR)
        system_kind = str(event.payload.get("source_kind") or family.value)
        system_id = stable_id("srcsys", context.workspace, event.connector_name)
        object_id = stable_id("srcobj", system_id, event.external_id)
        revision_id = stable_id("rev", object_id, revision_hash)
        artifact_id = stable_id("art", revision_id, "payload.json")
        document_id = stable_id(
            "ldoc",
            context.workspace,
            f"{event.connector_name}:{event.external_id}",
        )
        snapshot = event.acl_snapshot
        if snapshot is None:
            raise ValidationError("connector event requires an ACL snapshot")
        ingest_context = context.model_copy(
            update={
                "acl_scopes": sorted(
                    set(context.acl_scopes).union(event.acl_scopes)
                )
            }
        )
        self._store.upsert_acl_snapshot(
            ingest_context,
            object_id,
            snapshot,
            classification,
        )
        if self._store.has_revision(ingest_context, object_id, revision_hash):
            return IngestResult(
                status="unchanged",
                source_object_id=object_id,
                revision_id=revision_id,
                artifact_id=artifact_id,
                document_id=document_id,
            )

        cas_uri = self._content_store.put(payload_bytes, suffix=".json")
        title, body, locator = _event_content(event, family)
        extraction_id = new_id("ext")
        units = self._content_units(
            event,
            family,
            extraction_id=extraction_id,
            document_id=document_id,
            artifact_id=artifact_id,
            title=title,
            body=body,
            locator=locator,
            acl_snapshot_id=snapshot.id,
            classification=classification,
        )
        packet = DocumentPacket(
            workspace_id=context.workspace,
            source_object=SourceObject(
                id=object_id,
                system_id=system_id,
                system_name=event.connector_name,
                system_kind=system_kind,
                external_id=event.external_id,
                object_type="message",
                canonical_uri=_event_uri(event, family),
                classification=classification,
                acl_scopes=list(event.acl_scopes),
                acl_snapshot=snapshot,
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
            extraction=ExtractionRun(
                id=extraction_id,
                artifact_id=artifact_id,
                parser_name=f"{event.connector_name}-normalizer",
                parser_version="1.0",
                status="succeeded",
                quality_score=1.0,
                output_hash=sha256_bytes(body.encode("utf-8")),
                metadata={"operation": event.operation},
            ),
            units=units,
        )
        return self._store.ingest_packet(ingest_context, packet)

    def _content_units(
        self,
        event: ConnectorEvent,
        family: EventFamily,
        *,
        extraction_id: str,
        document_id: str,
        artifact_id: str,
        title: str,
        body: str,
        locator: EvidenceLocator,
        acl_snapshot_id: str,
        classification: DataClassification,
    ) -> list[ContentUnit]:
        if event.operation == "delete":
            return []
        normalized = normalize_text(body)
        return [
            ContentUnit(
                id=stable_id("unit", extraction_id, "0"),
                extraction_id=extraction_id,
                document_id=document_id,
                artifact_id=artifact_id,
                ordinal=0,
                unit_type="slack_message"
                if family is EventFamily.SLACK
                else "email_message",
                title=title,
                body=body,
                body_normalized=normalized,
                lexical_text=self._analyzer.analyze(
                    f"{title}\n{normalized}\n{event.external_id}"
                ),
                locator=locator,
                classification=classification,
                acl_scopes=list(event.acl_scopes),
                acl_snapshot_id=acl_snapshot_id,
                metadata={"connector": event.connector_name},
            )
        ]


def _event_content(
    event: ConnectorEvent,
    family: EventFamily,
) -> tuple[str, str, EvidenceLocator]:
    payload = event.payload
    title = str(payload.get("subject") or payload.get("title") or "Connector message")
    body = str(
        payload.get("text")
        or payload.get("content")
        or payload.get("body")
        or ""
    )
    match family:
        case EventFamily.SLACK:
            return (
                f"Slack {payload.get('conversation_id', '')} {payload.get('ts', '')}",
                str(payload.get("text", "")),
                EvidenceLocator(
                    type="slack_message",
                    data={
                        "workspace_id": payload.get("workspace_id"),
                        "conversation_id": payload.get("conversation_id"),
                        "ts": payload.get("ts"),
                        "thread_ts": payload.get("thread_ts"),
                    },
                ),
            )
        case EventFamily.MAIL:
            locator = EvidenceLocator(
                type="email_message",
                data={
                    "account_id": payload.get("account_id")
                    or payload.get("account"),
                    "mailbox": payload.get("mailbox"),
                    "message_id": payload.get("message_id"),
                    "uid": payload.get("uid") or payload.get("mail_internal_id"),
                },
            )
        case EventFamily.CONNECTOR:
            locator = EvidenceLocator(
                type="connector_object",
                data={
                    "connector": event.connector_name,
                    "external_id": event.external_id,
                },
            )
        case unreachable:
            assert_never(unreachable)
    return title, body, locator


def _event_uri(event: ConnectorEvent, family: EventFamily) -> str:
    match family:
        case EventFamily.SLACK:
            payload = event.payload
            return "slack://{}/{}/{}".format(
                payload.get("workspace_id", ""),
                payload.get("conversation_id", ""),
                payload.get("ts", ""),
            )
        case EventFamily.MAIL:
            message_id = str(event.payload.get("message_id") or event.external_id)
            return f"mail://{message_id}"
        case EventFamily.CONNECTOR:
            return f"connector://{event.connector_name}/{event.external_id}"
        case unreachable:
            assert_never(unreachable)
