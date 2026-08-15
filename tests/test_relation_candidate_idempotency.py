from __future__ import annotations

from pathlib import Path

from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.knowledge import KnowledgeEntity, RelationDerivation, RelationProposal
from kip.domain.models import SearchRequest
from kip.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_relation_candidate_fingerprint_is_idempotent_and_revision_bound(
    tmp_path: Path,
) -> None:
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
    repository = MemoryRepository()
    container = build_container(settings, repository=repository)
    context = container.application.operations.request_context(roles=["admin"])
    for entity in (
        KnowledgeEntity(
            id="ent_document",
            entity_type="Document",
            canonical_name="승인 공문",
        ),
        KnowledgeEntity(
            id="ent_decision",
            entity_type="Decision",
            canonical_name="참여율 변경",
        ),
    ):
        container.application.ontology_rag.create_entity(context, entity)
    (source / "승인.txt").write_text("참여율 변경을 기록한다.", encoding="utf-8")
    container.application.ingestion.sync_filesystem(context, "fixture")
    unit_id = container.application.retrieval.search(
        context,
        SearchRequest(query="참여율 변경 기록"),
    )[0].unit_id

    def proposal(revision: str) -> RelationProposal:
        return RelationProposal(
            subject_id="ent_document",
            predicate="records_decision",
            object_entity_id="ent_decision",
            ontology_version="core/1.0.0",
            evidence_unit_ids=(unit_id,),
            derivation=RelationDerivation(
                kind="model",
                name="miner",
                model="fixture",
                revision=revision,
            ),
        )

    first = container.application.ontology_rag.propose_relation(context, proposal("r1"))
    duplicate = container.application.ontology_rag.propose_relation(context, proposal("r1"))
    changed_revision = container.application.ontology_rag.propose_relation(
        context,
        proposal("r2"),
    )

    assert duplicate.id == first.id
    assert duplicate.fingerprint == first.fingerprint
    assert changed_revision.id != first.id
    assert len(repository.state.candidates) == 2
