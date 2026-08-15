from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.knowledge import (
    KnowledgeEntity,
    RelationDerivation,
    RelationProposal,
    intervals_overlap,
    normalize_entity_name,
)
from kip.domain.models import SearchRequest
from kip.errors import ValidationError
from kip.ontology import OntologyCatalog
from kip.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def _container(tmp_path: Path):
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
                        "acl_scope": "workspace:default",
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


def _entities(container, context):
    subject = container.application.ontology_rag.create_entity(
        context,
        KnowledgeEntity(
            id="ent_document",
            entity_type="OfficialLetter",
            canonical_name="  A과제   승인 공문 ",
            aliases=["A과제 공문", " 승인공문 "],
            acl_scopes=["workspace:default"],
        ),
    )
    decision = container.application.ontology_rag.create_entity(
        context,
        KnowledgeEntity(
            id="ent_decision",
            entity_type="ParticipationRateChange",
            canonical_name="A과제 참여율 변경",
            aliases=["참여율 변경"],
            acl_scopes=["workspace:default"],
        ),
    )
    return subject, decision


def test_entity_names_and_aliases_are_normalized_and_resolvable(tmp_path: Path) -> None:
    container = _container(tmp_path)
    context = container.application.operations.request_context(roles=["admin"])
    subject, _ = _entities(container, context)

    assert subject.canonical_name == "A과제 승인 공문"
    assert subject.aliases == ["A과제 공문", "승인공문"]
    assert normalize_entity_name(" Ａ과제\t승인 공문 ") == "a과제 승인 공문"
    assert [
        entity.id
        for entity in container.application.ontology_rag.resolve_entities(
            context,
            "참여율 변경 승인공문",
        )
    ] == ["ent_decision", "ent_document"]


def test_ontology_validates_inherited_domain_and_range() -> None:
    catalog = OntologyCatalog.load(ROOT / "ontology")

    spec = catalog.validate_relation(
        subject_type="OfficialLetter",
        predicate="records_decision",
        object_type="ParticipationRateChange",
    )

    assert spec.risk == "high"
    assert spec.review == "required"
    with pytest.raises(ValidationError, match="domain"):
        catalog.validate_relation(
            subject_type="Project",
            predicate="records_decision",
            object_type="ParticipationRateChange",
        )
    with pytest.raises(ValidationError, match="range"):
        catalog.validate_relation(
            subject_type="OfficialLetter",
            predicate="records_decision",
            object_type="Person",
        )


def test_relation_proposal_rejects_invalid_temporal_bounds() -> None:
    now = datetime.now(UTC)

    with pytest.raises(PydanticValidationError, match="valid_to"):
        RelationProposal(
            subject_id="ent_a",
            predicate="records_decision",
            object_entity_id="ent_b",
            ontology_version="core/1.0.0",
            evidence_unit_ids=("unit_1",),
            valid_from=now,
            valid_to=now - timedelta(seconds=1),
            derivation=RelationDerivation(
                kind="manual",
                name="operator",
                revision="v1",
            ),
        )


def test_interval_overlap_is_explicit_for_open_and_bounded_intervals() -> None:
    now = datetime.now(UTC)

    assert intervals_overlap(None, None, now, now + timedelta(days=1)) is True
    assert (
        intervals_overlap(
            now,
            now + timedelta(days=1),
            now + timedelta(days=1),
            now + timedelta(days=2),
        )
        is False
    )


def test_typed_candidate_contains_exact_evidence_risk_and_acl_intersection(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    context = container.application.operations.request_context(roles=["admin"])
    subject, decision = _entities(container, context)
    path = tmp_path / "source" / "승인.txt"
    path.write_text("A과제 참여율 변경을 승인하고 기록한다.", encoding="utf-8")
    container.application.ingestion.sync_filesystem(context, "fixture")
    unit_id = container.application.retrieval.search(
        context,
        SearchRequest(query="A과제 참여율 변경 승인"),
    )[0].unit_id

    candidate = container.application.ontology_rag.propose_relation(
        context,
        RelationProposal(
            subject_id=subject.id,
            predicate="records_decision",
            object_entity_id=decision.id,
            ontology_version="core/1.0.0",
            evidence_unit_ids=(unit_id,),
            confidence=0.93,
            derivation=RelationDerivation(
                kind="model",
                name="fixture-miner",
                model="fixture-model",
                revision="sha256:miner",
            ),
        ),
    )

    assert candidate.fingerprint.startswith("sha256:")
    assert candidate.review_risk == "high"
    assert candidate.evidence[0].content_unit_id == unit_id
    assert candidate.evidence[0].source_revision_sha256
    assert candidate.evidence[0].locator["type"] == "text_line_range"
    assertion = container.application.knowledge.review_approve(context, candidate.id)
    assert assertion.acl_scopes == ["workspace:default"]
    assert assertion.valid_from is None
    assert assertion.valid_to is None


def test_relation_proposal_rejects_domain_mismatch_before_persistence(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    context = container.application.operations.request_context(roles=["admin"])
    project = container.application.ontology_rag.create_entity(
        context,
        KnowledgeEntity(
            id="ent_project",
            entity_type="Project",
            canonical_name="A과제",
        ),
    )
    decision = container.application.ontology_rag.create_entity(
        context,
        KnowledgeEntity(
            id="ent_decision",
            entity_type="Decision",
            canonical_name="참여율 변경",
        ),
    )
    path = tmp_path / "source" / "근거.txt"
    path.write_text("A과제 참여율 변경 기록", encoding="utf-8")
    container.application.ingestion.sync_filesystem(context, "fixture")
    unit_id = container.application.retrieval.search(
        context,
        SearchRequest(query="A과제 참여율 변경"),
    )[0].unit_id

    with pytest.raises(ValidationError, match="domain"):
        container.application.ontology_rag.propose_relation(
            context,
            RelationProposal(
                subject_id=project.id,
                predicate="records_decision",
                object_entity_id=decision.id,
                ontology_version="core/1.0.0",
                evidence_unit_ids=(unit_id,),
                derivation=RelationDerivation(
                    kind="manual",
                    name="operator",
                    revision="v1",
                ),
            ),
        )


def test_overlapping_different_object_is_marked_as_contradiction(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    context = container.application.operations.request_context(roles=["admin"])
    subject, first_decision = _entities(container, context)
    second_decision = container.application.ontology_rag.create_entity(
        context,
        KnowledgeEntity(
            id="ent_decision_rejected",
            entity_type="ParticipationRateChange",
            canonical_name="A과제 참여율 변경 반려",
        ),
    )
    path = tmp_path / "source" / "결정.txt"
    path.write_text("A과제 참여율 변경 결정을 기록한다.", encoding="utf-8")
    container.application.ingestion.sync_filesystem(context, "fixture")
    unit_id = container.application.retrieval.search(
        context,
        SearchRequest(query="A과제 참여율 변경 결정"),
    )[0].unit_id

    def proposal(object_id: str, revision: str) -> RelationProposal:
        return RelationProposal(
            subject_id=subject.id,
            predicate="records_decision",
            object_entity_id=object_id,
            ontology_version="core/1.0.0",
            evidence_unit_ids=(unit_id,),
            derivation=RelationDerivation(
                kind="manual",
                name="reviewer",
                revision=revision,
            ),
        )

    first = container.application.ontology_rag.propose_relation(
        context,
        proposal(first_decision.id, "r1"),
    )
    assertion = container.application.knowledge.review_approve(context, first.id)
    conflicting = container.application.ontology_rag.propose_relation(
        context,
        proposal(second_decision.id, "r2"),
    )

    assert conflicting.contradicts_assertion_ids == [assertion.id]
