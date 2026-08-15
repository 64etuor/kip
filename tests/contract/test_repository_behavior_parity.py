from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.repository.postgres import PostgresRepository
from kip.domain.egress import DataClassification
from kip.domain.identity import AclSnapshot
from kip.domain.knowledge import AUTO_APPROVE_POLICY_PRINCIPAL, CandidateEvidence
from kip.domain.models import (
    Artifact,
    AssertionCandidate,
    ContentUnit,
    DocumentPacket,
    EvidenceLocator,
    ExtractionRun,
    GraphNeighborsRequest,
    GraphPathRequest,
    LogicalDocument,
    RequestContext,
    SearchRequest,
    SourceObject,
    SourceRevision,
)
from kip.errors import NotFoundError
from kip.ids import new_id
from kip.ports.repository import RepositoryPort

# Same env-guarded pattern as tests/integration/test_postgres_repository.py:
# skip the postgres side cleanly when no integration database is configured,
# while the memory side always runs.
URL = os.environ.get("KIP_TEST_POSTGRES_URL") or os.environ.get("KIP_DATABASE_URL")
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


@dataclass(frozen=True, slots=True)
class Ingested:
    object_id: str
    unit_id: str
    artifact_id: str
    document_id: str


@dataclass(frozen=True, slots=True)
class Harness:
    """Thin per-test wrapper bundling a repository with its workspace.

    Every scenario below runs the identical assertions against whichever
    backend the `harness` fixture parametrized, so a real behavior
    divergence between `MemoryRepository` and `PostgresRepository` shows up
    as one backend failing while the other passes.
    """

    repository: RepositoryPort
    workspace: str

    def context(
        self,
        *,
        principal_id: str = "principal_contract",
        acl_scopes: list[str] | None = None,
    ) -> RequestContext:
        return RequestContext(
            workspace=self.workspace,
            principal_id=principal_id,
            acl_scopes=(
                acl_scopes
                if acl_scopes is not None
                else [f"workspace:{self.workspace}"]
            ),
            request_id=new_id("req"),
        )

    def ingest(
        self,
        suffix: str,
        *,
        body: str,
        acl_scopes: list[str],
        classification: DataClassification = DataClassification.INTERNAL,
    ) -> Ingested:
        packet, ingested = _packet(
            self.workspace,
            suffix,
            body=body,
            acl_scopes=acl_scopes,
            classification=classification,
        )
        self.repository.ingestion.ingest_packet(
            self.context(acl_scopes=acl_scopes),
            packet,
        )
        return ingested


def _packet(
    workspace: str,
    suffix: str,
    *,
    body: str,
    acl_scopes: list[str],
    classification: DataClassification,
) -> tuple[DocumentPacket, Ingested]:
    # `PostgresIngestionStore.ingest_packet` requires a non-None ACL
    # snapshot whose scopes match the source object (ValidationError
    # otherwise), while `MemoryIngestionStore.ingest_packet` tolerates a
    # None snapshot and skips that check entirely -- a real divergence
    # (reported alongside this file). Always supplying a matching,
    # configuration-owned snapshot here stays inside the shared contract
    # both backends accept.
    token = f"{suffix}_{workspace}"
    object_id = f"obj_{token}"
    revision_id = f"rev_{token}"
    artifact_id = f"art_{token}"
    extraction_id = f"ext_{token}"
    document_id = f"ldoc_{token}"
    unit_id = f"unit_{token}"
    snapshot = AclSnapshot.configuration(
        snapshot_id=f"aclsnap_{token}",
        version="contract-v1",
        provider="contract-test",
        scopes=acl_scopes,
    )
    packet = DocumentPacket(
        workspace_id=workspace,
        source_object=SourceObject(
            id=object_id,
            system_id=f"srcsys_{token}",
            system_name=f"contract-fixture-{token}",
            system_kind="fixture",
            external_id=token,
            object_type="document",
            canonical_uri=f"fixture://{token}",
            classification=classification,
            acl_scopes=acl_scopes,
            acl_snapshot=snapshot,
        ),
        revision=SourceRevision(
            id=revision_id,
            object_id=object_id,
            revision_key="sha",
            sha256=f"sha_{token}",
        ),
        logical_document=LogicalDocument(
            id=document_id,
            stable_key=f"fixture:{token}",
            title=f"Fixture {suffix}",
        ),
        artifact=Artifact(
            id=artifact_id,
            revision_id=revision_id,
            file_name=f"{token}.txt",
            sha256=f"sha_{token}",
        ),
        extraction=ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name="fixture",
            parser_version="1",
            status="succeeded",
        ),
        units=[
            ContentUnit(
                id=unit_id,
                extraction_id=extraction_id,
                document_id=document_id,
                artifact_id=artifact_id,
                ordinal=0,
                unit_type="text",
                title=f"Fixture {suffix}",
                body=body,
                body_normalized=body,
                lexical_text=body,
                locator=EvidenceLocator(type="text", data={"token": token}),
                classification=classification,
                acl_scopes=acl_scopes,
                acl_snapshot_id=snapshot.id,
            )
        ],
    )
    ingested = Ingested(
        object_id=object_id,
        unit_id=unit_id,
        artifact_id=artifact_id,
        document_id=document_id,
    )
    return packet, ingested


@pytest.fixture(params=["memory", "postgres"])
def harness(request: pytest.FixtureRequest) -> Iterator[Harness]:
    if request.param == "memory":
        yield Harness(repository=MemoryRepository(), workspace="contract_memory")
        return

    if not URL:
        pytest.skip("PostgreSQL integration URL not configured")
    pytest.importorskip("psycopg")
    workspace = "test_" + uuid.uuid4().hex[:12]
    repository = PostgresRepository(str(URL))
    repository.operations.migrate(MIGRATIONS_DIR)
    try:
        yield Harness(repository=repository, workspace=workspace)
    finally:
        import psycopg

        with (
            psycopg.connect(str(URL), autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("DELETE FROM kip.workspaces WHERE slug=%s", (workspace,))


def test_ingest_lexical_search_and_read_unit_round_trip(harness: Harness) -> None:
    scope = f"workspace:{harness.workspace}"
    ingested = harness.ingest(
        "roundtrip",
        body="contract renewal evidence text",
        acl_scopes=[scope],
    )
    context = harness.context(acl_scopes=[scope])

    hits = harness.repository.retrieval.search(
        context,
        SearchRequest(query="renewal", limit=10),
        "renewal",
    )
    assert [hit.unit_id for hit in hits] == [ingested.unit_id]

    unit = harness.repository.evidence.get_content_unit(context, ingested.unit_id)
    assert unit.id == ingested.unit_id
    assert unit.body == "contract renewal evidence text"

    view = harness.repository.evidence.get_artifact(context, ingested.artifact_id)
    assert view.artifact.id == ingested.artifact_id
    assert view.source_object is not None
    assert view.source_object.id == ingested.object_id


def test_acl_scope_denial_on_search_and_read(harness: Harness) -> None:
    scope_alpha = f"workspace:{harness.workspace}:alpha"
    scope_beta = f"workspace:{harness.workspace}:beta"
    alpha = harness.ingest(
        "acl-alpha",
        body="alpha denial fixture text",
        acl_scopes=[scope_alpha],
    )
    beta = harness.ingest(
        "acl-beta",
        body="beta denial fixture text",
        acl_scopes=[scope_beta],
    )
    alpha_context = harness.context(
        principal_id="alpha_principal",
        acl_scopes=[scope_alpha],
    )

    # Both units match the shared lexical term, so a widened-visibility
    # regression would still surface the beta unit here.
    alpha_hits = harness.repository.retrieval.search(
        alpha_context,
        SearchRequest(query="denial", limit=10),
        "denial",
    )
    assert {hit.unit_id for hit in alpha_hits} == {alpha.unit_id}

    assert (
        harness.repository.evidence.get_content_unit(alpha_context, alpha.unit_id).id
        == alpha.unit_id
    )
    with pytest.raises(NotFoundError):
        harness.repository.evidence.get_content_unit(alpha_context, beta.unit_id)


def test_graph_neighbors_direction_and_approved_only_after_revoke(
    harness: Harness,
) -> None:
    scope = f"workspace:{harness.workspace}"
    context = harness.context(acl_scopes=[scope])
    evidence = harness.ingest(
        "graph-evidence",
        body="graph neighbor evidence text",
        acl_scopes=[scope],
    )
    subject = f"node_subject_{harness.workspace}"
    object_out = f"node_object_out_{harness.workspace}"
    object_in = f"node_object_in_{harness.workspace}"

    out_candidate = harness.repository.knowledge.save_candidate(
        context,
        AssertionCandidate(
            id=new_id("cand"),
            subject_id=subject,
            predicate="contract_amends_out",
            object_entity_id=object_out,
            origin="human",
            confidence=0.9,
            ontology_version="core/1.0.0",
            evidence=[CandidateEvidence(content_unit_id=evidence.unit_id)],
        ),
    )
    in_candidate = harness.repository.knowledge.save_candidate(
        context,
        AssertionCandidate(
            id=new_id("cand"),
            subject_id=object_in,
            predicate="contract_amends_in",
            object_entity_id=subject,
            origin="human",
            confidence=0.9,
            ontology_version="core/1.0.0",
            evidence=[CandidateEvidence(content_unit_id=evidence.unit_id)],
        ),
    )
    out_assertion = harness.repository.knowledge.approve_candidate(
        context, out_candidate.id, context.principal_id
    )
    in_assertion = harness.repository.knowledge.approve_candidate(
        context, in_candidate.id, context.principal_id
    )

    out_edges = harness.repository.knowledge.graph_neighbors(
        context, GraphNeighborsRequest(node_id=subject, direction="out")
    )
    assert [edge.assertion_id for edge in out_edges] == [out_assertion.id]

    in_edges = harness.repository.knowledge.graph_neighbors(
        context, GraphNeighborsRequest(node_id=subject, direction="in")
    )
    assert [edge.assertion_id for edge in in_edges] == [in_assertion.id]

    both_edges = harness.repository.knowledge.graph_neighbors(
        context, GraphNeighborsRequest(node_id=subject, direction="both")
    )
    assert {edge.assertion_id for edge in both_edges} == {
        out_assertion.id,
        in_assertion.id,
    }

    harness.repository.knowledge.revoke_assertion(
        context, out_assertion.id, context.principal_id, "contract test revoke"
    )

    approved_only_edges = harness.repository.knowledge.graph_neighbors(
        context,
        GraphNeighborsRequest(node_id=subject, direction="out", approved_only=True),
    )
    assert approved_only_edges == []

    unfiltered_edges = harness.repository.knowledge.graph_neighbors(
        context,
        GraphNeighborsRequest(node_id=subject, direction="out", approved_only=False),
    )
    assert [edge.assertion_id for edge in unfiltered_edges] == [out_assertion.id]
    assert unfiltered_edges[0].status == "revoked"


def test_graph_path_bounds_results_when_many_direct_paths_exist(
    harness: Harness,
) -> None:
    # A star of two-hop routes (from_node -> mid_i -> to_node) is used
    # instead of many parallel direct from_node->to_node edges: the memory
    # adapter's BFS cap (`kip.adapters.repository.memory.knowledge
    # ._bounded_paths`) is only re-checked between dequeuing distinct
    # frontier nodes, so a single node's direct fan-out is not truncated by
    # it, while `PostgresKnowledgeStore.graph_path`'s SQL `LIMIT` truncates
    # any result set. Routing every path through a distinct intermediate
    # node makes both backends re-check their cap once per completed path,
    # so the "bounded, not exhaustive" contract is exercised identically on
    # both sides.
    scope = f"workspace:{harness.workspace}"
    context = harness.context(acl_scopes=[scope])
    evidence = harness.ingest(
        "graph-path-evidence",
        body="graph path cap evidence text",
        acl_scopes=[scope],
    )
    from_node = f"path_from_{harness.workspace}"
    to_node = f"path_to_{harness.workspace}"
    total_paths = 30
    for index in range(total_paths):
        mid_node = f"path_mid_{index}_{harness.workspace}"
        for hop, (subject_id, object_entity_id) in enumerate(
            ((from_node, mid_node), (mid_node, to_node))
        ):
            candidate = harness.repository.knowledge.save_candidate(
                context,
                AssertionCandidate(
                    id=new_id("cand"),
                    subject_id=subject_id,
                    predicate=f"cap_hop{hop}_{index}",
                    object_entity_id=object_entity_id,
                    origin="human",
                    confidence=0.9,
                    ontology_version="core/1.0.0",
                    evidence=[CandidateEvidence(content_unit_id=evidence.unit_id)],
                ),
            )
            harness.repository.knowledge.approve_candidate(
                context, candidate.id, context.principal_id
            )

    paths = harness.repository.knowledge.graph_path(
        context,
        GraphPathRequest(from_node_id=from_node, to_node_id=to_node, max_depth=2),
    )

    # Both backends cap the number of returned paths well below the number
    # of two-hop routes actually available (currently 20 on both). The
    # exact cap is an implementation detail that may change independently
    # on either side, so this only asserts the shared "bounded, not
    # exhaustive" contract rather than a hardcoded count.
    assert 0 < len(paths) < total_paths
    for path in paths:
        assert path.depth == 2
        assert path.node_ids[0] == from_node
        assert path.node_ids[-1] == to_node
        assert len(path.assertion_ids) == 2


def test_assertion_candidate_propose_approve_appears_in_graph_then_revoke_disappears(
    harness: Harness,
) -> None:
    scope = f"workspace:{harness.workspace}"
    context = harness.context(acl_scopes=[scope])
    evidence = harness.ingest(
        "lifecycle-evidence",
        body="candidate lifecycle evidence text",
        acl_scopes=[scope],
    )
    subject = f"lifecycle_subject_{harness.workspace}"
    object_entity = f"lifecycle_object_{harness.workspace}"

    proposed = harness.repository.knowledge.save_candidate(
        context,
        AssertionCandidate(
            id=new_id("cand"),
            subject_id=subject,
            predicate="lifecycle_predicate",
            object_entity_id=object_entity,
            origin="human",
            confidence=0.8,
            ontology_version="core/1.0.0",
            evidence=[CandidateEvidence(content_unit_id=evidence.unit_id)],
        ),
    )
    assert proposed.status == "proposed"
    assert (
        harness.repository.knowledge.graph_neighbors(
            context, GraphNeighborsRequest(node_id=subject, direction="out")
        )
        == []
    )

    approved = harness.repository.knowledge.approve_candidate(
        context, proposed.id, context.principal_id
    )
    assert approved.status == "active"

    edges = harness.repository.knowledge.graph_neighbors(
        context, GraphNeighborsRequest(node_id=subject, direction="out")
    )
    assert [edge.assertion_id for edge in edges] == [approved.id]

    revoked = harness.repository.knowledge.revoke_assertion(
        context, approved.id, context.principal_id, "contract lifecycle revoke"
    )
    assert revoked.status == "revoked"

    assert (
        harness.repository.knowledge.graph_neighbors(
            context, GraphNeighborsRequest(node_id=subject, direction="out")
        )
        == []
    )


def test_predicate_review_precision_counts_human_decisions_and_excludes_auto_approve_marker(
    harness: Harness,
) -> None:
    scope = f"workspace:{harness.workspace}"
    context = harness.context(acl_scopes=[scope])
    evidence = harness.ingest(
        "precision-evidence",
        body="predicate precision fixture text",
        acl_scopes=[scope],
    )
    predicate = "precision_fixture_predicate"
    subject = f"precision_subject_{harness.workspace}"

    def _candidate(suffix: str) -> AssertionCandidate:
        return AssertionCandidate(
            id=new_id("cand"),
            subject_id=subject,
            predicate=predicate,
            object_entity_id=f"precision_object_{suffix}_{harness.workspace}",
            origin="human",
            confidence=0.9,
            ontology_version="core/1.0.0",
            evidence=[CandidateEvidence(content_unit_id=evidence.unit_id)],
        )

    approved_one = harness.repository.knowledge.save_candidate(
        context, _candidate("approved-1")
    )
    approved_two = harness.repository.knowledge.save_candidate(
        context, _candidate("approved-2")
    )
    rejected_one = harness.repository.knowledge.save_candidate(
        context, _candidate("rejected-1")
    )
    auto_approved_one = harness.repository.knowledge.save_candidate(
        context, _candidate("auto-1")
    )

    harness.repository.knowledge.approve_candidate(
        context, approved_one.id, context.principal_id
    )
    harness.repository.knowledge.approve_candidate(
        context, approved_two.id, context.principal_id
    )
    harness.repository.knowledge.reject_candidate(
        context, rejected_one.id, context.principal_id
    )
    # Approved by the calibrated auto-approve policy, exactly as
    # `OntologyRagUseCases._maybe_auto_approve` invokes it: through a context
    # whose principal is the dedicated policy marker, never a human or job
    # principal, so the precision query can tell the two apart.
    policy_context = harness.context(
        principal_id=AUTO_APPROVE_POLICY_PRINCIPAL,
        acl_scopes=[scope],
    )
    harness.repository.knowledge.approve_candidate(
        policy_context,
        auto_approved_one.id,
        AUTO_APPROVE_POLICY_PRINCIPAL,
        f"{AUTO_APPROVE_POLICY_PRINCIPAL} precision=1.0000 sample=0",
    )

    stats = harness.repository.knowledge.predicate_review_precision(context, predicate)

    assert stats.approved == 2
    assert stats.rejected == 1
    assert stats.reviewed == 3
    assert stats.precision == pytest.approx(2 / 3)


def test_job_enqueue_claim_complete_lifecycle(harness: Harness) -> None:
    context = harness.context()

    job_id = harness.repository.jobs.enqueue_job(
        context, "contract.fixture", {"note": "contract-parity"}
    )
    queued = harness.repository.jobs.list_jobs(context, status="queued")
    assert job_id in {job.id for job in queued}

    claimed = harness.repository.jobs.claim_job(context, "contract-worker")
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.status == "running"
    assert claimed.attempts == 1

    # No other queued job is available for a second worker to claim.
    assert harness.repository.jobs.claim_job(context, "contract-worker-2") is None

    harness.repository.jobs.record_job_result(context, job_id, {"outcome": "ok"})
    harness.repository.jobs.complete_job(context, job_id)

    succeeded = [
        job
        for job in harness.repository.jobs.list_jobs(context, status="succeeded")
        if job.id == job_id
    ]
    assert len(succeeded) == 1
    assert succeeded[0].payload.get("result") == {"outcome": "ok"}
