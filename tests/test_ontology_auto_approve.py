"""Calibrated review tiers: audited, measured, revocable auto-approval.

Covers the fail-closed eligibility gate (predicate risk/review tier,
confidence, measured precision over a minimum human-reviewed sample), the
policy marker recorded on approval, revocability, and the no-self-reinforcement
guarantee on the precision statistic itself. The human review path (used
whenever any axis fails) is exercised by the same fixtures and by
`tests/test_ontology_review_governance.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.generation import GenerationUsage, ModelRevision
from kip.domain.knowledge import (
    AUTO_APPROVE_POLICY_PRINCIPAL,
    KnowledgeEntity,
    MinedRelationProposal,
    RelationMiningRequest,
    RelationMiningResult,
)
from kip.domain.models import AssertionCandidate, CandidateEvidence, SearchRequest
from kip.ids import new_id
from kip.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
_SCOPES = ["workspace:default", "group:ontology-reviewers"]


class ConfigurableRelationMiner:
    """Emits exactly one configurable relation proposal per mine() call."""

    name = "auto-approve-fixture-miner"
    model = "fixture-model"
    revision = "fixture-revision"

    def __init__(
        self,
        *,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str,
        confidence: float | None,
    ) -> None:
        self.subject_entity_id = subject_entity_id
        self.predicate = predicate
        self.object_entity_id = object_entity_id
        self.confidence = confidence
        self.requests: list[RelationMiningRequest] = []

    def mine(self, request: RelationMiningRequest) -> RelationMiningResult:
        self.requests.append(request)
        evidence_id = request.evidence[0].id
        return RelationMiningResult(
            entities=(),
            relations=(
                MinedRelationProposal(
                    subject_entity_id=self.subject_entity_id,
                    predicate=self.predicate,
                    object_entity_id=self.object_entity_id,
                    evidence_ids=(evidence_id,),
                    confidence=self.confidence,
                ),
            ),
            model=ModelRevision(provider="local", model=self.model, revision=self.revision),
            usage=GenerationUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            provider_request_id="req_auto_approve_fixture",
        )


def _container(
    tmp_path: Path,
    miner: ConfigurableRelationMiner,
    *,
    auto_approve: dict[str, object] | None = None,
):
    source = tmp_path / "source"
    source.mkdir()
    raw: dict[str, object] = {
        "search": {"semantic_enabled": False},
        "graph": {"backend": "memory"},
        "security": {"allow_remote_model_egress": False},
        "models": {
            "generation": {
                "enabled": True,
                "provider": "local",
                "base_url": "http://127.0.0.1:7998",
                "model": "unused-answer-model",
                "revision": "unused-answer-revision",
                "allowed_classifications": ["internal"],
            }
        },
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
    }
    if auto_approve is not None:
        raw["ontology"] = {"auto_approve": auto_approve}
    settings = Settings(
        project_root=ROOT,
        config_path=tmp_path / "kip.toml",
        raw=raw,
        environment="test",
        workspace="default",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )
    return build_container(
        settings,
        repository=MemoryRepository(),
        relation_miner=miner,
    )


def _seed(container, tmp_path: Path):
    context = container.application.operations.request_context(
        acl_scopes=_SCOPES, roles=["admin"]
    )
    container.application.ontology_rag.create_entity(
        context,
        KnowledgeEntity(
            id="ent_letter",
            entity_type="OfficialLetter",
            canonical_name="A과제 승인 공문",
            acl_scopes=["group:ontology-reviewers"],
        ),
    )
    (tmp_path / "source" / "승인.txt").write_text(
        "A과제 승인 공문 근거 문서입니다.",
        encoding="utf-8",
    )
    container.application.ingestion.sync_filesystem(context, "fixture")
    hit = container.application.retrieval.search(
        context,
        SearchRequest(query="A과제 승인 공문 근거"),
    )[0]
    return context, hit.unit_id


def _entity(container, context, entity_id: str, entity_type: str, name: str) -> None:
    container.application.ontology_rag.create_entity(
        context,
        KnowledgeEntity(
            id=entity_id,
            entity_type=entity_type,
            canonical_name=name,
            acl_scopes=["group:ontology-reviewers"],
        ),
    )


def _build_review_history(
    container,
    context,
    unit_id: str,
    *,
    subject_id: str,
    predicate: str,
    object_type: str,
    approved: int,
    rejected: int,
) -> None:
    """Seeds human approve/reject decisions the precision gate measures.

    Each decision cites the SAME evidence unit but a distinct object entity,
    so every candidate gets a distinct fingerprint without needing separate
    ingested documents per decision.
    """
    knowledge = container.application.knowledge
    counter = 0

    def _decide(status: str) -> None:
        nonlocal counter
        counter += 1
        object_id = f"ent_hist_{predicate}_{counter}"
        _entity(container, context, object_id, object_type, f"이력객체{counter}")
        candidate = knowledge.create_candidate(
            context,
            AssertionCandidate(
                id=new_id("cand"),
                subject_id=subject_id,
                predicate=predicate,
                object_entity_id=object_id,
                origin="human",
                confidence=0.9,
                ontology_version="core/1.0.0",
                evidence=[CandidateEvidence(content_unit_id=unit_id)],
            ),
        )
        if status == "approved":
            knowledge.review_approve(context, candidate.id)
        else:
            knowledge.review_reject(context, candidate.id)

    for _ in range(approved):
        _decide("approved")
    for _ in range(rejected):
        _decide("rejected")


def test_qualifying_candidate_auto_approves_with_marker_and_stays_revocable(
    tmp_path: Path,
) -> None:
    miner = ConfigurableRelationMiner(
        subject_entity_id="ent_letter",
        predicate="authored_by",
        object_entity_id="ent_author_new",
        confidence=0.9,
    )
    # Auto-approve is opt-in by default (container.py): this test exercises
    # the enabled path, so it must request it explicitly.
    container = _container(tmp_path, miner, auto_approve={"enabled": True})
    context, unit_id = _seed(container, tmp_path)
    _entity(container, context, "ent_author_new", "Person", "신규 작성자")
    _build_review_history(
        container,
        context,
        unit_id,
        subject_id="ent_letter",
        predicate="authored_by",
        object_type="Person",
        approved=19,
        rejected=1,
    )

    summary = container.application.ontology_rag.process_mining(context, [unit_id])

    assert len(summary.auto_approved) == 1
    record = summary.auto_approved[0]
    assert record.predicate == "authored_by"
    assert record.precision == pytest.approx(19 / 20)
    assert record.sample_size == 20

    [candidate] = [
        item
        for item in summary.relation_candidates
        if item.object_entity_id == "ent_author_new"
    ]
    assert candidate.status == "approved"
    assert candidate.id == record.candidate_id
    assert candidate.review_note is not None
    assert candidate.review_note.startswith(AUTO_APPROVE_POLICY_PRINCIPAL)
    assert "precision=0.9500" in candidate.review_note
    assert "sample=20" in candidate.review_note

    knowledge = container.application.knowledge
    assertion = knowledge.get_assertion(context, record.assertion_id)
    assert assertion.status == "active"
    assert assertion.source_candidate_id == record.candidate_id
    assert assertion.subject_id == "ent_letter"
    assert assertion.object_entity_id == "ent_author_new"

    # Revocable via the ordinary review-revoke path, like any other approval.
    revoked = knowledge.revoke_assertion(context, assertion.id, "정책 재검토 필요")
    assert revoked.status == "revoked"
    assert revoked.revoked_by == context.principal_id
    assert revoked.revocation_note == "정책 재검토 필요"


def test_medium_risk_conditional_review_predicate_never_auto_approves(
    tmp_path: Path,
) -> None:
    miner = ConfigurableRelationMiner(
        subject_entity_id="ent_letter",
        predicate="belongs_to_project",
        object_entity_id="ent_project_new",
        confidence=0.99,
    )
    container = _container(tmp_path, miner)
    context, unit_id = _seed(container, tmp_path)
    _entity(container, context, "ent_project_new", "ResearchProject", "신규 과제")
    _build_review_history(
        container,
        context,
        unit_id,
        subject_id="ent_letter",
        predicate="belongs_to_project",
        object_type="ResearchProject",
        approved=25,
        rejected=0,
    )

    summary = container.application.ontology_rag.process_mining(context, [unit_id])

    assert summary.auto_approved == []
    [candidate] = [
        item
        for item in summary.relation_candidates
        if item.object_entity_id == "ent_project_new"
    ]
    assert candidate.status == "proposed"


def test_high_risk_review_required_predicate_never_auto_approves(
    tmp_path: Path,
) -> None:
    miner = ConfigurableRelationMiner(
        subject_entity_id="ent_letter",
        predicate="records_decision",
        object_entity_id="ent_decision_new",
        confidence=0.99,
    )
    container = _container(tmp_path, miner)
    context, unit_id = _seed(container, tmp_path)
    _entity(container, context, "ent_decision_new", "ParticipationRateChange", "신규 결정")
    _build_review_history(
        container,
        context,
        unit_id,
        subject_id="ent_letter",
        predicate="records_decision",
        object_type="ParticipationRateChange",
        approved=25,
        rejected=0,
    )

    summary = container.application.ontology_rag.process_mining(context, [unit_id])

    assert summary.auto_approved == []
    [candidate] = [
        item
        for item in summary.relation_candidates
        if item.object_entity_id == "ent_decision_new"
    ]
    assert candidate.status == "proposed"

    # The human review flow downstream is completely unaffected.
    approved = container.application.knowledge.review_approve(context, candidate.id)
    assert approved.status == "active"


def test_low_confidence_candidate_is_not_auto_approved(tmp_path: Path) -> None:
    miner = ConfigurableRelationMiner(
        subject_entity_id="ent_letter",
        predicate="authored_by",
        object_entity_id="ent_author_lowconf",
        confidence=0.5,
    )
    container = _container(tmp_path, miner)
    context, unit_id = _seed(container, tmp_path)
    _entity(container, context, "ent_author_lowconf", "Person", "낮은신뢰")
    _build_review_history(
        container,
        context,
        unit_id,
        subject_id="ent_letter",
        predicate="authored_by",
        object_type="Person",
        approved=19,
        rejected=1,
    )

    summary = container.application.ontology_rag.process_mining(context, [unit_id])

    assert summary.auto_approved == []


def test_candidate_without_confidence_is_not_auto_approved(tmp_path: Path) -> None:
    miner = ConfigurableRelationMiner(
        subject_entity_id="ent_letter",
        predicate="authored_by",
        object_entity_id="ent_author_noconf",
        confidence=None,
    )
    container = _container(tmp_path, miner)
    context, unit_id = _seed(container, tmp_path)
    _entity(container, context, "ent_author_noconf", "Person", "신뢰도없음")
    _build_review_history(
        container,
        context,
        unit_id,
        subject_id="ent_letter",
        predicate="authored_by",
        object_type="Person",
        approved=19,
        rejected=1,
    )

    summary = container.application.ontology_rag.process_mining(context, [unit_id])

    assert summary.auto_approved == []


def test_insufficient_review_sample_blocks_auto_approve(tmp_path: Path) -> None:
    miner = ConfigurableRelationMiner(
        subject_entity_id="ent_letter",
        predicate="authored_by",
        object_entity_id="ent_author_smallsample",
        confidence=0.95,
    )
    container = _container(tmp_path, miner)
    context, unit_id = _seed(container, tmp_path)
    _entity(container, context, "ent_author_smallsample", "Person", "적은표본")
    # Precision is a perfect 1.0, but the sample (5) is under the default
    # min_reviewed floor (20).
    _build_review_history(
        container,
        context,
        unit_id,
        subject_id="ent_letter",
        predicate="authored_by",
        object_type="Person",
        approved=5,
        rejected=0,
    )

    summary = container.application.ontology_rag.process_mining(context, [unit_id])

    assert summary.auto_approved == []


def test_low_precision_blocks_auto_approve(tmp_path: Path) -> None:
    miner = ConfigurableRelationMiner(
        subject_entity_id="ent_letter",
        predicate="authored_by",
        object_entity_id="ent_author_lowprecision",
        confidence=0.95,
    )
    container = _container(tmp_path, miner)
    context, unit_id = _seed(container, tmp_path)
    _entity(container, context, "ent_author_lowprecision", "Person", "낮은정밀도")
    # Sample (20) clears the floor, but precision (0.75) is under the
    # default min_precision floor (0.95).
    _build_review_history(
        container,
        context,
        unit_id,
        subject_id="ent_letter",
        predicate="authored_by",
        object_type="Person",
        approved=15,
        rejected=5,
    )

    summary = container.application.ontology_rag.process_mining(context, [unit_id])

    assert summary.auto_approved == []


def test_disabled_policy_never_auto_approves_even_when_fully_qualified(
    tmp_path: Path,
) -> None:
    miner = ConfigurableRelationMiner(
        subject_entity_id="ent_letter",
        predicate="authored_by",
        object_entity_id="ent_author_disabled",
        confidence=0.95,
    )
    container = _container(tmp_path, miner, auto_approve={"enabled": False})
    context, unit_id = _seed(container, tmp_path)
    _entity(container, context, "ent_author_disabled", "Person", "비활성")
    _build_review_history(
        container,
        context,
        unit_id,
        subject_id="ent_letter",
        predicate="authored_by",
        object_type="Person",
        approved=19,
        rejected=1,
    )

    summary = container.application.ontology_rag.process_mining(context, [unit_id])

    assert summary.auto_approved == []
    [candidate] = [
        item
        for item in summary.relation_candidates
        if item.object_entity_id == "ent_author_disabled"
    ]
    assert candidate.status == "proposed"


def test_auto_approve_defaults_off_without_explicit_opt_in(tmp_path: Path) -> None:
    """A deployment that never configures `[ontology.auto_approve]` at all
    must never silently auto-promote candidates, even when every other axis
    (predicate risk/review tier, confidence, measured precision) fully
    qualifies. `container.py` defaults `enabled` to False both when the
    section is entirely absent and when it is present without `enabled`.
    """
    miner = ConfigurableRelationMiner(
        subject_entity_id="ent_letter",
        predicate="authored_by",
        object_entity_id="ent_author_default_off",
        confidence=0.95,
    )
    # No `auto_approve=` override at all: exercises the container's own
    # default, not a test-supplied one.
    container = _container(tmp_path, miner)
    context, unit_id = _seed(container, tmp_path)
    _entity(container, context, "ent_author_default_off", "Person", "기본값비활성")
    _build_review_history(
        container,
        context,
        unit_id,
        subject_id="ent_letter",
        predicate="authored_by",
        object_type="Person",
        approved=19,
        rejected=1,
    )

    summary = container.application.ontology_rag.process_mining(context, [unit_id])

    assert summary.auto_approved == []
    [candidate] = [
        item
        for item in summary.relation_candidates
        if item.object_entity_id == "ent_author_default_off"
    ]
    assert candidate.status == "proposed"


def test_precision_excludes_auto_approved_decisions_from_their_own_denominator(
    tmp_path: Path,
) -> None:
    miner = ConfigurableRelationMiner(
        subject_entity_id="ent_letter",
        predicate="authored_by",
        object_entity_id="ent_author_selfreinforce",
        confidence=0.95,
    )
    # Auto-approve is opt-in by default (container.py): this test exercises
    # the enabled path, so it must request it explicitly.
    container = _container(tmp_path, miner, auto_approve={"enabled": True})
    context, unit_id = _seed(container, tmp_path)
    _entity(container, context, "ent_author_selfreinforce", "Person", "자기강화")
    _build_review_history(
        container,
        context,
        unit_id,
        subject_id="ent_letter",
        predicate="authored_by",
        object_type="Person",
        approved=19,
        rejected=1,
    )

    before = container.repository.knowledge.predicate_review_precision(
        context, "authored_by"
    )
    assert (before.approved, before.rejected, before.reviewed) == (19, 1, 20)

    summary = container.application.ontology_rag.process_mining(context, [unit_id])
    assert len(summary.auto_approved) == 1

    after = container.repository.knowledge.predicate_review_precision(
        context, "authored_by"
    )
    # The auto-approved decision just made is excluded from its own
    # denominator: the count is unchanged, not 20 -> 21.
    assert (after.approved, after.rejected, after.reviewed) == (19, 1, 20)
    assert after.precision == before.precision
