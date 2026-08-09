from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.domain.identity import AclSnapshot
from kip.domain.models import (
    ApprovedAssertion,
    Artifact,
    ContentUnit,
    DocumentPacket,
    EvidenceLocator,
    ExtractionRun,
    GraphNeighborsRequest,
    LogicalDocument,
    RequestContext,
    SearchRequest,
    SourceObject,
    SourceRevision,
)
from kip.errors import NotFoundError

NOW = datetime.now(UTC)


def _packet(snapshot: AclSnapshot) -> DocumentPacket:
    return DocumentPacket(
        workspace_id="acme",
        source_object=SourceObject(
            id="srcobj_1",
            system_id="srcsys_1",
            system_name="slack",
            system_kind="slack",
            external_id="message-1",
            object_type="message",
            canonical_uri="slack://acme/C1/1.0",
            acl_scopes=["workspace:acme"],
            acl_snapshot=snapshot,
        ),
        revision=SourceRevision(
            id="rev_1",
            object_id="srcobj_1",
            revision_key="sha",
            sha256="sha",
        ),
        logical_document=LogicalDocument(
            id="ldoc_1",
            stable_key="slack:message-1",
            title="승인 공지",
        ),
        artifact=Artifact(
            id="art_1",
            revision_id="rev_1",
            file_name="payload.json",
            sha256="sha",
        ),
        extraction=ExtractionRun(
            id="ext_1",
            artifact_id="art_1",
            parser_name="fixture",
            parser_version="1",
            status="succeeded",
        ),
        units=[
            ContentUnit(
                id="unit_1",
                extraction_id="ext_1",
                document_id="ldoc_1",
                artifact_id="art_1",
                ordinal=0,
                unit_type="message",
                title="승인 공지",
                body="A과제 변경은 승인되었다.",
                body_normalized="A과제 변경은 승인되었다.",
                lexical_text="A과제 변경 승인",
                locator=EvidenceLocator(type="message", data={"id": "message-1"}),
                acl_scopes=["workspace:acme"],
                acl_snapshot_id=snapshot.id,
            )
        ],
    )


def _context() -> RequestContext:
    return RequestContext(
        workspace="acme",
        principal_id="user-1",
        acl_scopes=["workspace:acme"],
        request_id="req_1",
    )


def test_stale_dynamic_acl_snapshot_is_hidden_from_search_read_and_graph() -> None:
    repository = MemoryRepository()
    stale = AclSnapshot(
        id="aclsnap_stale",
        version="slack-v1",
        provider="slack",
        scopes=["workspace:acme"],
        captured_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(minutes=1),
    )
    repository.ingestion.ingest_packet(_context(), _packet(stale))
    repository.state.assertions["ast_1"] = ApprovedAssertion(
        id="ast_1",
        subject_id="project-a",
        predicate="amends",
        object_entity_id="plan-a",
        ontology_version="core/1.0.0",
        acl_scopes=["workspace:acme"],
        evidence_unit_ids=["unit_1"],
    )

    assert repository.retrieval.search(
        _context(), SearchRequest(query="승인"), "승인"
    ) == []
    with pytest.raises(NotFoundError):
        repository.evidence.get_content_unit(_context(), "unit_1")
    assert repository.knowledge.graph_neighbors(
        _context(), GraphNeighborsRequest(node_id="project-a")
    ) == []


def test_fresh_dynamic_acl_snapshot_is_visible() -> None:
    repository = MemoryRepository()
    fresh = AclSnapshot(
        id="aclsnap_fresh",
        version="slack-v2",
        provider="slack",
        scopes=["workspace:acme"],
        captured_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )
    repository.ingestion.ingest_packet(_context(), _packet(fresh))

    assert [
        hit.unit_id
        for hit in repository.retrieval.search(
            _context(), SearchRequest(query="승인"), "승인"
        )
    ] == ["unit_1"]
    assert repository.evidence.get_content_unit(_context(), "unit_1").id == "unit_1"


def test_configuration_owned_acl_snapshot_does_not_expire() -> None:
    static = AclSnapshot.configuration(
        snapshot_id="aclsnap_static",
        version="config-v1",
        provider="filesystem:company-nas",
        scopes=["workspace:acme"],
        captured_at=NOW - timedelta(days=365),
    )

    assert static.configuration_owned is True
    assert static.is_fresh(NOW + timedelta(days=3650)) is True
