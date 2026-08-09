from __future__ import annotations

from pathlib import Path

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.generation import GenerationUsage, ModelRevision
from kip.domain.knowledge import (
    KnowledgeEntity,
    MinedEntityProposal,
    MinedRelationProposal,
    RelationMiningRequest,
    RelationMiningResult,
)
from kip.domain.models import AnswerRequest, GraphNeighborsRequest, SearchRequest
from kip.errors import NotFoundError
from kip.settings import Settings
from kip.worker import run_worker

ROOT = Path(__file__).resolve().parents[1]


class RecordingRelationMiner:
    name = "fixture-miner"
    model = "fixture-model"
    revision = "fixture-revision"

    def __init__(self) -> None:
        self.requests: list[RelationMiningRequest] = []

    def mine(self, request: RelationMiningRequest) -> RelationMiningResult:
        self.requests.append(request)
        evidence_id = request.evidence[0].id
        return RelationMiningResult(
            entities=(
                MinedEntityProposal(
                    entity_type="Project",
                    canonical_name="신규 과제",
                    aliases=["새 과제"],
                    evidence_ids=(evidence_id,),
                    confidence=0.82,
                ),
            ),
            relations=(
                MinedRelationProposal(
                    subject_entity_id="ent_document",
                    predicate="records_decision",
                    object_entity_id="ent_decision",
                    evidence_ids=(evidence_id,),
                    confidence=0.94,
                ),
            ),
            model=ModelRevision(
                provider="local",
                model=self.model,
                revision=self.revision,
            ),
            usage=GenerationUsage(
                input_tokens=20,
                output_tokens=10,
                total_tokens=30,
            ),
            provider_request_id="req_fixture",
        )


def _container(
    tmp_path: Path,
    miner: RecordingRelationMiner,
    *,
    generation_enabled: bool = True,
):
    source = tmp_path / "source"
    source.mkdir()
    settings = Settings(
        project_root=ROOT,
        config_path=tmp_path / "kip.toml",
        raw={
            "search": {"semantic_enabled": False},
            "graph": {"backend": "memory"},
            "security": {"allow_remote_model_egress": False},
            "models": {
                "generation": {
                    "enabled": generation_enabled,
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
        },
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
        acl_scopes=["workspace:default", "group:ontology-reviewers"]
    )
    container.application.ontology_rag.create_entity(
        context,
        KnowledgeEntity(
            id="ent_document",
            entity_type="OfficialLetter",
            canonical_name="A과제 승인 공문",
            acl_scopes=["group:ontology-reviewers"],
        ),
    )
    container.application.ontology_rag.create_entity(
        context,
        KnowledgeEntity(
            id="ent_decision",
            entity_type="ParticipationRateChange",
            canonical_name="A과제 참여율 변경",
            acl_scopes=["group:ontology-reviewers"],
        ),
    )
    (tmp_path / "source" / "승인.txt").write_text(
        "신규 과제의 A과제 참여율 변경을 승인 공문에 기록한다.",
        encoding="utf-8",
    )
    container.application.ingestion.sync_filesystem(context, "fixture")
    hit = container.application.retrieval.search(
        context,
        SearchRequest(query="신규 과제 참여율 변경"),
    )[0]
    return context, hit.unit_id


def test_mining_job_is_idempotent_and_candidates_require_review(
    tmp_path: Path,
) -> None:
    miner = RecordingRelationMiner()
    container = _container(tmp_path, miner)
    context, unit_id = _seed(container, tmp_path)

    first_job = container.application.ontology_rag.enqueue_mining(
        context,
        [unit_id],
    )
    duplicate_job = container.application.ontology_rag.enqueue_mining(
        context,
        [unit_id],
    )
    run_worker(container, once=True)

    assert duplicate_job == first_job
    assert len(miner.requests) == 1
    assert container.application.operations.list_jobs(context)[0].status == "succeeded"
    entity_candidates = container.application.ontology_rag.list_entity_candidates(
        context
    )
    relation_candidates = container.application.knowledge.list_candidates(context)
    assert len(entity_candidates) == 1
    assert len(relation_candidates) == 1
    assert container.application.knowledge.graph_neighbors(
        context,
        GraphNeighborsRequest(node_id="ent_document"),
    ) == []

    approved_relation = container.application.knowledge.review_approve(
        context,
        relation_candidates[0].id,
    )
    approved_entity = container.application.ontology_rag.approve_entity_candidate(
        context,
        entity_candidates[0].id,
    )

    assert approved_relation.evidence_unit_ids == [unit_id]
    assert approved_entity.canonical_name == "신규 과제"
    assert approved_entity.acl_scopes == ["group:ontology-reviewers"]
    assert len(
        container.application.knowledge.graph_neighbors(
            context,
            GraphNeighborsRequest(node_id="ent_document"),
        )
    ) == 1


def test_candidates_are_hidden_before_review_from_unauthorized_principals(
    tmp_path: Path,
) -> None:
    miner = RecordingRelationMiner()
    container = _container(tmp_path, miner)
    context, unit_id = _seed(container, tmp_path)
    container.application.ontology_rag.process_mining(context, [unit_id])
    candidate = container.application.ontology_rag.list_entity_candidates(context)[0]
    denied = container.application.operations.request_context(
        principal_id="principal_denied",
        acl_scopes=["workspace:default"],
    )

    assert container.application.ontology_rag.list_entity_candidates(denied) == []
    assert container.application.knowledge.list_candidates(denied) == []
    with pytest.raises(NotFoundError):
        container.application.ontology_rag.get_entity_candidate(
            denied,
            candidate.id,
        )


def test_search_and_answer_do_not_trigger_relation_mining(tmp_path: Path) -> None:
    miner = RecordingRelationMiner()
    container = _container(tmp_path, miner, generation_enabled=False)
    context, _ = _seed(container, tmp_path)

    container.application.retrieval.search(
        context,
        SearchRequest(query="신규 과제"),
    )
    response = container.application.answering.answer(
        context,
        AnswerRequest(query="신규 과제는 무엇인가?"),
    )

    assert response.retrieval_mode == "extractive"
    assert miner.requests == []
