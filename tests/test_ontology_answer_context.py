from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from kip.adapters.repository.memory import MemoryRepository
from kip.api import create_app
from kip.container import build_container
from kip.domain.generation import (
    GeneratedClaim,
    GenerationRequest,
    GenerationResult,
    GenerationUsage,
    ModelRevision,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)
from kip.domain.knowledge import KnowledgeEntity, RelationDerivation, RelationProposal
from kip.domain.models import AnswerRequest, GraphNeighborsRequest, SearchRequest
from kip.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


class RecordingAnswerGenerator:
    name = "recording-answer"
    provider = "local"
    model = "fixture-answer-model"
    revision = "fixture-answer-revision"

    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return GenerationResult(
            claims=(
                GeneratedClaim(
                    text="비밀별의 결정은 참여율을 30%로 변경하는 것이다.",
                    evidence_ids=(request.evidence[0].id,),
                    certainty="supported",
                ),
            ),
            model=ModelRevision(
                provider=self.provider,
                model=self.model,
                revision=self.revision,
            ),
            usage=GenerationUsage(
                input_tokens=30,
                output_tokens=12,
                total_tokens=42,
            ),
            provider_request_id="req_answer_graph",
        )

    def generate_structured(
        self,
        request: StructuredGenerationRequest,
    ) -> StructuredGenerationResult:
        raise AssertionError("answering must use the grounded answer contract")


def _container(
    tmp_path: Path,
    *,
    generator: RecordingAnswerGenerator | None = None,
    acl_scope: str = "workspace:default",
):
    source = tmp_path / "source"
    source.mkdir()
    generation = {
        "enabled": generator is not None,
        "provider": "local",
        "base_url": "http://127.0.0.1:7998",
        "model": generator.model if generator else "disabled-model",
        "revision": generator.revision if generator else "disabled-revision",
        "allowed_classifications": ["internal"],
    }
    settings = Settings(
        project_root=ROOT,
        config_path=tmp_path / "kip.toml",
        raw={
            "search": {"semantic_enabled": False},
            "graph": {"backend": "memory"},
            "models": {"generation": generation},
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
                        "acl_scope": acl_scope,
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
        generator=generator,
    )


def _seed_relation(
    container,
    tmp_path: Path,
    *,
    context=None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
):
    context = context or container.application.operations.request_context(
        roles=["admin"]
    )
    document = container.application.ontology_rag.create_entity(
        context,
        KnowledgeEntity(
            id="ent_secret_letter",
            entity_type="OfficialLetter",
            canonical_name="오로라 승인 공문",
            aliases=["비밀별"],
            acl_scopes=list(context.acl_scopes),
        ),
    )
    decision = container.application.ontology_rag.create_entity(
        context,
        KnowledgeEntity(
            id="ent_rate_decision",
            entity_type="ParticipationRateChange",
            canonical_name="참여율 30% 변경",
            acl_scopes=list(context.acl_scopes),
        ),
    )
    (tmp_path / "source" / "결정.txt").write_text(
        "정식 결정은 참여율을 30%로 변경하며 승인되었다.",
        encoding="utf-8",
    )
    container.application.ingestion.sync_filesystem(context, "fixture")
    unit_id = container.application.retrieval.search(
        context,
        SearchRequest(query="정식 결정 참여율"),
    )[0].unit_id
    candidate = container.application.ontology_rag.propose_relation(
        context,
        RelationProposal(
            subject_id=document.id,
            predicate="records_decision",
            object_entity_id=decision.id,
            ontology_version="core/1.0.0",
            evidence_unit_ids=(unit_id,),
            valid_from=valid_from,
            valid_to=valid_to,
            derivation=RelationDerivation(
                kind="manual",
                name="ontology-answer-test",
                revision="v1",
            ),
        ),
    )
    return context, unit_id, candidate


def test_only_approved_graph_evidence_can_rescue_a_lexical_miss(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    context, unit_id, candidate = _seed_relation(container, tmp_path)
    request = AnswerRequest(query="비밀별 결정은 무엇이야?", limit=5)

    before_review = container.application.answering.answer(context, request)
    container.application.knowledge.review_approve(context, candidate.id)
    after_review = container.application.answering.answer(context, request)

    assert before_review.refused is True
    assert before_review.ontology_context is None
    assert after_review.refused is False
    assert after_review.citations[0].unit_id == unit_id
    assert after_review.ontology_context is not None
    assert after_review.ontology_context.entities[0].id == "ent_secret_letter"
    assert after_review.ontology_context.edges[0].predicate == "records_decision"
    assert after_review.ontology_context.evidence_unit_ids == [unit_id]


def test_expired_and_future_assertions_are_not_current_graph_context(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    for name, valid_from, valid_to in (
        ("expired", now - timedelta(days=2), now - timedelta(days=1)),
        ("future", now + timedelta(days=1), None),
    ):
        case_root = tmp_path / name
        case_root.mkdir()
        container = _container(case_root)
        context, _, candidate = _seed_relation(
            container,
            case_root,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        container.application.knowledge.review_approve(context, candidate.id)

        edges = container.application.knowledge.graph_neighbors(
            context,
            GraphNeighborsRequest(node_id="ent_secret_letter"),
        )
        response = container.application.answering.answer(
            context,
            AnswerRequest(query="비밀별 결정은 무엇이야?", limit=5),
        )

        assert edges == []
        assert response.refused is True
        assert response.ontology_context is None


def test_graph_context_is_acl_filtered_before_entity_resolution(tmp_path: Path) -> None:
    container = _container(tmp_path, acl_scope="group:secret")
    owner = container.application.operations.request_context(
        acl_scopes=["group:secret"], roles=["admin"]
    )
    _, _, candidate = _seed_relation(container, tmp_path, context=owner)
    container.application.knowledge.review_approve(owner, candidate.id)
    denied = container.application.operations.request_context(
        principal_id="principal_denied",
        acl_scopes=["workspace:default"],
    )

    response = container.application.answering.answer(
        denied,
        AnswerRequest(query="비밀별 결정은 무엇이야?", limit=5),
    )

    assert response.refused is True
    assert response.ontology_context is None
    assert response.citations == []


def test_generated_answer_receives_only_approved_graph_relations(
    tmp_path: Path,
) -> None:
    generator = RecordingAnswerGenerator()
    container = _container(tmp_path, generator=generator)
    context, unit_id, candidate = _seed_relation(container, tmp_path)
    container.application.knowledge.review_approve(context, candidate.id)

    response = container.application.answering.answer(
        context,
        AnswerRequest(query="비밀별 결정은 무엇이야?", limit=5),
    )

    assert response.refused is False
    assert response.retrieval_mode == "generated"
    assert generator.requests[0].relations[0].predicate == "records_decision"
    assert generator.requests[0].relations[0].evidence_ids == (unit_id,)


def test_stale_approved_evidence_is_removed_from_graph_context(tmp_path: Path) -> None:
    container = _container(tmp_path)
    context, _, candidate = _seed_relation(container, tmp_path)
    container.application.knowledge.review_approve(context, candidate.id)
    (tmp_path / "source" / "결정.txt").write_text(
        "정식 결정은 아직 검토 중이다.",
        encoding="utf-8",
    )

    response = container.application.answering.answer(
        context,
        AnswerRequest(query="비밀별 결정은 무엇이야?", limit=5),
    )

    assert response.refused is True
    assert response.refusal_reason == "no_fresh_evidence"
    assert response.ontology_context is None
    assert response.citations == []


def test_ontology_context_returns_current_multi_hop_path_with_exact_evidence(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    context = container.application.operations.request_context(roles=["admin"])
    for entity_id, name, alias in (
        ("ent_latest", "최신 계약서", "최신계약"),
        ("ent_middle", "중간 계약서", "중간계약"),
        ("ent_original", "최초 계약서", "최초계약"),
    ):
        container.application.ontology_rag.create_entity(
            context,
            KnowledgeEntity(
                id=entity_id,
                entity_type="Document",
                canonical_name=name,
                aliases=[alias],
            ),
        )
    (tmp_path / "source" / "첫개정.txt").write_text(
        "첫 번째 개정은 납기 조건을 변경했다.",
        encoding="utf-8",
    )
    (tmp_path / "source" / "둘째개정.txt").write_text(
        "두 번째 개정은 지급 조건을 변경했다.",
        encoding="utf-8",
    )
    container.application.ingestion.sync_filesystem(context, "fixture")
    first_unit = container.application.retrieval.search(
        context,
        SearchRequest(query="첫 번째 개정 납기"),
    )[0].unit_id
    second_unit = container.application.retrieval.search(
        context,
        SearchRequest(query="두 번째 개정 지급"),
    )[0].unit_id
    for subject, target, unit_id, revision in (
        ("ent_latest", "ent_middle", second_unit, "v2"),
        ("ent_middle", "ent_original", first_unit, "v1"),
    ):
        candidate = container.application.ontology_rag.propose_relation(
            context,
            RelationProposal(
                subject_id=subject,
                predicate="amends",
                object_entity_id=target,
                ontology_version="core/1.0.0",
                evidence_unit_ids=(unit_id,),
                derivation=RelationDerivation(
                    kind="manual",
                    name="path-test",
                    revision=revision,
                ),
            ),
        )
        container.application.knowledge.review_approve(context, candidate.id)

    bundle = container.application.ontology_context.build(
        context,
        "최신계약과 최초계약의 변경 경로",
    )

    assert bundle.context is not None
    assert bundle.context.paths[0].node_ids == [
        "ent_latest",
        "ent_middle",
        "ent_original",
    ]
    assert bundle.context.paths[0].depth == 2
    assert set(bundle.context.evidence_unit_ids) == {first_unit, second_unit}
    assert {item.unit.id for item in bundle.evidence} == {first_unit, second_unit}


def test_rest_answer_and_ontology_context_share_the_same_approved_graph(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    context, unit_id, candidate = _seed_relation(container, tmp_path)
    assertion = container.application.knowledge.review_approve(
        context,
        candidate.id,
    )
    client = TestClient(create_app(container))

    context_response = client.post(
        "/v1/ontology/context",
        json={"query": "비밀별 결정은 무엇이야?"},
    )
    answer_response = client.post(
        "/v1/answer",
        json={"query": "비밀별 결정은 무엇이야?", "limit": 5},
    )

    assert context_response.status_code == 200
    assert context_response.json()["data"]["edges"][0]["assertion_id"] == assertion.id
    assert answer_response.status_code == 200
    answer = answer_response.json()["data"]
    assert answer["ontology_context"]["edges"][0]["assertion_id"] == assertion.id
    assert answer["citations"][0]["unit_id"] == unit_id
