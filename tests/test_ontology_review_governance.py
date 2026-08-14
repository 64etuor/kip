from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest
import yaml

from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.knowledge import (
    KnowledgeEntity,
    RelationDerivation,
    RelationProposal,
)
from kip.domain.models import (
    AnswerRequest,
    AssertionCandidate,
    CandidateEvidence,
    GraphNeighborsRequest,
    SearchRequest,
)
from kip.errors import ConflictError, ValidationError
from kip.ids import new_id
from kip.ontology import FALLBACK_EVIDENCE_REQUIRED_PREDICATES, OntologyCatalog
from kip.settings import Settings

ROOT = Path(__file__).resolve().parents[1]

_SCOPES = ["workspace:default", "group:ontology-reviewers"]


@pytest.fixture()
def governance_container(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    settings = Settings(
        project_root=ROOT,
        config_path=tmp_path / "kip.toml",
        raw={
            "search": {"semantic_enabled": False},
            "graph": {"backend": "memory"},
            "sources": {
                "filesystem": [
                    {
                        "name": "fixture",
                        "root": str(source),
                        "enabled": True,
                        "read_only": True,
                        "settle_seconds": 0,
                        "include_extensions": [".txt"],
                        "exclude_globs": [],
                        "acl_scope": "group:ontology-reviewers",
                        "classification": "internal",
                    }
                ]
            },
            "parsers": {"hwp": {"order": ["paired_pdf"]}},
        },
        environment="test",
        workspace="default",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )
    return build_container(settings, repository=MemoryRepository())


def _seed(container, tmp_path: Path):
    context = container.application.operations.request_context(acl_scopes=_SCOPES)
    for entity_id, entity_type, name in (
        ("ent_letter", "OfficialLetter", "A과제 승인 공문"),
        ("ent_agreement", "Agreement", "A과제 협약서"),
        ("ent_decision", "ParticipationRateChange", "A과제 참여율 변경"),
        ("ent_project", "ResearchProject", "A과제"),
        ("ent_person", "Person", "김담당"),
    ):
        container.application.ontology_rag.create_entity(
            context,
            KnowledgeEntity(
                id=entity_id,
                entity_type=entity_type,
                canonical_name=name,
                acl_scopes=["group:ontology-reviewers"],
            ),
        )
    (tmp_path / "source" / "승인.txt").write_text(
        "A과제 참여율 변경을 승인 공문에 기록한다.",
        encoding="utf-8",
    )
    container.application.ingestion.sync_filesystem(context, "fixture")
    unit_id = container.application.retrieval.search(
        context,
        SearchRequest(query="참여율 변경 승인"),
    )[0].unit_id
    return context, unit_id


def _candidate(
    unit_id: str,
    *,
    predicate: str = "records_decision",
    subject_id: str = "ent_letter",
    object_entity_id: str = "ent_decision",
    confidence: float | None = None,
    evidence: bool = True,
) -> AssertionCandidate:
    return AssertionCandidate(
        id=new_id("cand"),
        subject_id=subject_id,
        predicate=predicate,
        object_entity_id=object_entity_id,
        origin="human",
        confidence=confidence,
        ontology_version="core/1.0.0",
        evidence=(
            [CandidateEvidence(content_unit_id=unit_id)] if evidence else []
        ),
    )


def test_evidence_enforcement_set_is_derived_from_predicates_yaml() -> None:
    payload = yaml.safe_load(
        (ROOT / "ontology/core/predicates.yaml").read_text(encoding="utf-8")
    )
    expected = {
        name
        for name, definition in payload["predicates"].items()
        if definition["review"] == "required" or definition["risk"] == "high"
    }
    catalog = OntologyCatalog.load(ROOT / "ontology")

    assert catalog.evidence_required_predicates() == expected
    # Store-level fail-closed floor is a floor, not a snapshot: predicates
    # released later via the ontology discovery approval flow always default
    # to review == "required", so the derived set can only grow. The
    # hardcoded fallback must stay a subset of (never diverge above) the
    # derived set, and the shipped tree must still derive at least it.
    assert expected >= FALLBACK_EVIDENCE_REQUIRED_PREDICATES
    # The originally diverging predicates are enforced again.
    assert {"responds_to", "records_decision"} <= expected


@pytest.mark.parametrize(
    ("predicate", "subject_id", "object_entity_id"),
    [
        ("records_decision", "ent_letter", "ent_decision"),
        ("responds_to", "ent_letter", "ent_agreement"),
        ("amends", "ent_letter", "ent_agreement"),
    ],
)
def test_evidence_free_approval_fails_for_review_required_predicates(
    governance_container,
    tmp_path: Path,
    predicate: str,
    subject_id: str,
    object_entity_id: str,
) -> None:
    context, unit_id = _seed(governance_container, tmp_path)
    candidate = governance_container.application.knowledge.create_candidate(
        context,
        _candidate(
            unit_id,
            predicate=predicate,
            subject_id=subject_id,
            object_entity_id=object_entity_id,
            evidence=False,
        ),
    )
    # The catalog-derived risk overrides the caller-supplied default.
    assert candidate.review_risk == "high"

    with pytest.raises(ValidationError, match="requires evidence"):
        governance_container.application.knowledge.review_approve(
            context,
            candidate.id,
        )


def test_revoked_assertion_leaves_all_approved_surfaces(
    governance_container,
    tmp_path: Path,
) -> None:
    context, unit_id = _seed(governance_container, tmp_path)
    knowledge = governance_container.application.knowledge
    candidate = knowledge.create_candidate(context, _candidate(unit_id))
    assertion = knowledge.review_approve(context, candidate.id)
    assert (
        len(
            knowledge.graph_neighbors(
                context,
                GraphNeighborsRequest(node_id="ent_letter"),
            )
        )
        == 1
    )
    ontology_context = governance_container.application.ontology_context.build(
        context,
        "A과제 승인 공문의 참여율 변경",
    )
    assert ontology_context.context is not None

    with pytest.raises(ValidationError, match="revocation note"):
        knowledge.revoke_assertion(context, assertion.id, None)
    with pytest.raises(ValidationError, match="revocation note"):
        knowledge.revoke_assertion(context, assertion.id, "   ")

    revoked = knowledge.revoke_assertion(context, assertion.id, "근거 판단 오류")

    assert revoked.status == "revoked"
    assert revoked.revoked_by == context.principal_id
    assert revoked.revocation_note == "근거 판단 오류"
    assert revoked.revoked_at is not None
    # Approved-only surfaces no longer see the assertion.
    assert (
        knowledge.graph_neighbors(
            context,
            GraphNeighborsRequest(node_id="ent_letter"),
        )
        == []
    )
    assert (
        governance_container.application.ontology_context.build(
            context,
            "A과제 승인 공문의 참여율 변경",
        ).context
        is None
    )
    # The record remains for audit; a second revoke conflicts.
    assert knowledge.get_assertion(context, assertion.id).status == "revoked"
    with pytest.raises(ConflictError):
        knowledge.revoke_assertion(context, assertion.id, "중복 철회")


def test_approve_with_supersede_marks_contradicted_assertion(
    governance_container,
    tmp_path: Path,
) -> None:
    context, unit_id = _seed(governance_container, tmp_path)
    knowledge = governance_container.application.knowledge
    governance_container.application.ontology_rag.create_entity(
        context,
        KnowledgeEntity(
            id="ent_decision_b",
            entity_type="ParticipationRateChange",
            canonical_name="B과제 참여율 변경",
            acl_scopes=["group:ontology-reviewers"],
        ),
    )
    first = knowledge.review_approve(
        context,
        knowledge.create_candidate(context, _candidate(unit_id)).id,
    )
    conflicting = governance_container.application.ontology_rag.propose_relation(
        context,
        RelationProposal(
            subject_id="ent_letter",
            predicate="records_decision",
            object_entity_id="ent_decision_b",
            ontology_version="core/1.0.0",
            evidence_unit_ids=(unit_id,),
            derivation=RelationDerivation(
                kind="manual", name="reviewer", revision="1"
            ),
        ),
    )
    assert conflicting.contradicts_assertion_ids == [first.id]

    replacement = knowledge.review_approve(
        context,
        conflicting.id,
        supersede_contradicted=True,
    )

    superseded = knowledge.get_assertion(context, first.id)
    assert superseded.status == "superseded"
    assert superseded.superseded_by == replacement.id
    edges = knowledge.graph_neighbors(
        context,
        GraphNeighborsRequest(node_id="ent_letter"),
    )
    assert [edge.assertion_id for edge in edges] == [replacement.id]


def test_supersede_flag_requires_a_recorded_contradiction(
    governance_container,
    tmp_path: Path,
) -> None:
    context, unit_id = _seed(governance_container, tmp_path)
    knowledge = governance_container.application.knowledge
    candidate = knowledge.create_candidate(context, _candidate(unit_id))

    with pytest.raises(ValidationError, match="does not contradict"):
        knowledge.review_approve(
            context,
            candidate.id,
            supersede_contradicted=True,
        )


def test_candidate_listing_is_enriched_ordered_and_filterable(
    governance_container,
    tmp_path: Path,
) -> None:
    context, unit_id = _seed(governance_container, tmp_path)
    knowledge = governance_container.application.knowledge
    low = knowledge.create_candidate(
        context,
        _candidate(
            unit_id,
            predicate="authored_by",
            subject_id="ent_letter",
            object_entity_id="ent_person",
            confidence=0.99,
        ),
    )
    medium = knowledge.create_candidate(
        context,
        _candidate(
            unit_id,
            predicate="belongs_to_project",
            subject_id="ent_letter",
            object_entity_id="ent_project",
            confidence=0.8,
        ),
    )
    high_low_confidence = knowledge.create_candidate(
        context,
        _candidate(unit_id, predicate="records_decision", confidence=0.4),
    )
    high_high_confidence = knowledge.create_candidate(
        context,
        _candidate(
            unit_id,
            predicate="responds_to",
            subject_id="ent_letter",
            object_entity_id="ent_agreement",
            confidence=0.9,
        ),
    )

    listing = knowledge.candidate_listing(context)

    assert listing.schema_version == "kip.assertion-candidate-listing.v1"
    assert listing.total == 4
    assert [item.id for item in listing.items] == [
        high_high_confidence.id,
        high_low_confidence.id,
        medium.id,
        low.id,
    ]
    first = listing.items[0]
    assert first.subject_display_name == "A과제 승인 공문"
    assert first.object_display_name == "A과제 협약서"
    assert first.predicate_label_ko == "회신 대응"
    assert first.predicate_description
    preview = first.evidence_previews[0]
    assert preview.content_unit_id == unit_id
    assert preview.readable is True
    assert preview.snippet is not None
    assert preview.snippet.startswith("A과제 참여율 변경")

    filtered = knowledge.candidate_listing(context, predicate="records_decision")
    assert [item.id for item in filtered.items] == [high_low_confidence.id]
    assert filtered.total == 1

    by_subject = knowledge.candidate_listing(context, subject_id="ent_letter")
    assert by_subject.total == 4

    # A principal without the evidence scope sees nothing.
    denied = governance_container.application.operations.request_context(
        principal_id="principal_denied",
        acl_scopes=["workspace:default"],
    )
    assert knowledge.candidate_listing(denied).total == 0


def test_candidate_assertions_appear_only_with_explicit_flag(
    governance_container,
    tmp_path: Path,
) -> None:
    context, unit_id = _seed(governance_container, tmp_path)
    knowledge = governance_container.application.knowledge
    approved = knowledge.review_approve(
        context,
        knowledge.create_candidate(context, _candidate(unit_id)).id,
    )
    pending = knowledge.create_candidate(
        context,
        _candidate(
            unit_id,
            predicate="responds_to",
            subject_id="ent_letter",
            object_entity_id="ent_agreement",
        ),
    )
    build = governance_container.application.ontology_context.build

    without_flag = build(context, "A과제 승인 공문의 참여율 변경")
    assert without_flag.context is not None
    assert without_flag.context.candidates == []

    with_flag = build(
        context,
        "A과제 승인 공문의 참여율 변경",
        include_candidates=True,
    )
    assert with_flag.context is not None
    labeled = with_flag.context.candidates
    assert [item.candidate_id for item in labeled] == [pending.id]
    assert all(item.status == "proposed" for item in labeled)
    # Candidates never merge into approved edges or their evidence set.
    assert [edge.assertion_id for edge in with_flag.context.edges] == [approved.id]
    assert set(with_flag.context.evidence_unit_ids) == {unit_id}

    answer = governance_container.application.answering.answer(
        context,
        AnswerRequest(
            query="A과제 승인 공문의 참여율 변경",
            include_candidate_assertions=True,
        ),
    )
    assert answer.ontology_context is not None
    assert [
        item.candidate_id for item in answer.ontology_context.candidates
    ] == [pending.id]

    plain = governance_container.application.answering.answer(
        context,
        AnswerRequest(query="A과제 승인 공문의 참여율 변경"),
    )
    assert plain.ontology_context is None or plain.ontology_context.candidates == []


def test_mcp_exposes_revocation_and_job_status_over_the_same_services(
    governance_container,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from kip.mcp_server import create_server

    context, unit_id = _seed(governance_container, tmp_path)
    knowledge = governance_container.application.knowledge
    assertion = knowledge.review_approve(
        context,
        knowledge.create_candidate(context, _candidate(unit_id)).id,
    )
    job_id = governance_container.application.ingestion.enqueue_sync(
        context, "fixture"
    )
    monkeypatch.setattr(
        "kip.mcp_server.build_container", lambda: governance_container
    )
    monkeypatch.setenv(
        "KIP_ACL_SCOPES", "workspace:default,group:ontology-reviewers"
    )
    monkeypatch.setenv("KIP_PRINCIPAL_ID", context.principal_id)
    server = create_server()

    async def invoke(tool: str, arguments: dict[str, object]) -> object:
        result = await server.call_tool(tool, arguments)
        return json.loads(result[0][0].text)

    revoked = anyio.run(
        lambda: invoke(
            "kip_ontology_assertion_revoke",
            {"assertion_id": assertion.id, "note": "근거 재검토 필요"},
        )
    )
    assert revoked["status"] == "revoked"
    assert revoked["revocation_note"] == "근거 재검토 필요"

    jobs = anyio.run(lambda: invoke("kip_jobs", {"status": "queued"}))
    assert isinstance(jobs, list)
    assert [job["id"] for job in jobs] == [job_id]

    candidates = anyio.run(
        lambda: invoke("kip_ontology_candidates", {"status": "approved"})
    )
    assert candidates["relations_total"] == 1
    approved_view = candidates["relations"][0]
    assert approved_view["subject_display_name"] == "A과제 승인 공문"
    assert approved_view["predicate_label_ko"] == "의사결정 기록"
