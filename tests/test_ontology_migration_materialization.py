from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.knowledge import KnowledgeEntity, RelationDerivation, RelationProposal
from kip.domain.models import GraphNeighborsRequest, RequestContext, SearchRequest
from kip.errors import ValidationError
from kip.ids import new_id
from kip.ontology_migration import OntologyMigration
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


def _copy_release(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(ROOT / "ontology", target)
    return target


def _rename_predicate(
    release: Path,
    *,
    source: str,
    targets: tuple[str, ...],
) -> None:
    predicate_path = release / "core/predicates.yaml"
    predicate_payload = yaml.safe_load(predicate_path.read_text(encoding="utf-8"))
    definitions = predicate_payload["predicates"]
    definition = definitions.pop(source)
    for target in targets:
        definitions[target] = dict(definition)
    predicate_payload["version"] = "2.0.0"
    predicate_path.write_text(
        yaml.safe_dump(predicate_payload, sort_keys=False),
        encoding="utf-8",
    )

    policy_path = release / "policies/review-policy.yaml"
    policy_payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    required = policy_payload["human_review_required"]["predicates"]
    policy_payload["human_review_required"]["predicates"] = [
        target if item == source else item
        for item in required
        for target in (targets if item == source else (item,))
    ]
    policy_path.write_text(
        yaml.safe_dump(policy_payload, sort_keys=False),
        encoding="utf-8",
    )


def _seed_approved_assertion(container, tmp_path: Path):
    context = container.application.operations.request_context()
    for entity in (
        KnowledgeEntity(
            id="doc_new",
            entity_type="Document",
            canonical_name="Amendment",
            acl_scopes=["workspace:default"],
        ),
        KnowledgeEntity(
            id="doc_old",
            entity_type="Document",
            canonical_name="Original agreement",
            acl_scopes=["workspace:default"],
        ),
    ):
        container.application.ontology_rag.create_entity(context, entity)
    (tmp_path / "source" / "amendment.txt").write_text(
        "The amendment changes the delivery date in the original agreement.",
        encoding="utf-8",
    )
    container.application.ingestion.sync_filesystem(context, "fixture")
    unit_id = container.application.retrieval.search(
        context,
        SearchRequest(query="changes delivery date"),
    )[0].unit_id
    candidate = container.application.ontology_rag.propose_relation(
        context,
        RelationProposal(
            subject_id="doc_new",
            predicate="amends",
            object_entity_id="doc_old",
            ontology_version="core/1.0.0",
            evidence_unit_ids=(unit_id,),
            derivation=RelationDerivation(
                kind="manual",
                name="migration-fixture",
                revision="v1",
            ),
        ),
    )
    assertion = container.application.knowledge.review_approve(
        context,
        candidate.id,
    )
    return context, unit_id, assertion


def _migration(*, operation: str, targets: tuple[str, ...], review: bool = True):
    return OntologyMigration.model_validate(
        {
            "schema_version": "kip.ontology-migration.v1",
            "from_version": "core/1.0.0",
            "to_version": "core/2.0.0",
            "operations": [
                {
                    "operation": operation,
                    "symbol_kind": "predicate",
                    "sources": ["amends"],
                    "targets": list(targets),
                    "review_required": review,
                }
            ],
        }
    )


def test_rename_materializes_review_candidate_with_exact_lineage_and_deduplicates(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    context, unit_id, source_assertion = _seed_approved_assertion(container, tmp_path)
    before = _copy_release(tmp_path, "before")
    after = _copy_release(tmp_path, "after")
    _rename_predicate(after, source="amends", targets=("revises",))
    migration = _migration(operation="rename", targets=("revises",))

    first = container.application.ontology_migrations.materialize(
        context,
        before,
        after,
        migration,
    )
    second = container.application.ontology_migrations.materialize(
        context,
        before,
        after,
        migration,
    )

    assert first.schema_version == "kip.ontology-migration-materialization.v1"
    assert first.source_assertion_count == 1
    assert first.created_candidate_count == 1
    assert first.existing_candidate_count == 0
    assert second.created_candidate_count == 0
    assert second.existing_candidate_count == 1
    assert second.candidate_ids == first.candidate_ids
    candidate = container.application.knowledge.get_candidate(
        context,
        first.candidate_ids[0],
    )
    assert candidate.status == "proposed"
    assert candidate.predicate == "revises"
    assert candidate.ontology_version == "core/2.0.0"
    assert candidate.evidence[0].content_unit_id == unit_id
    assert candidate.evidence[0].source_revision_sha256
    assert candidate.evidence[0].locator["type"] == "text_line_range"
    assert candidate.derivation is not None
    assert candidate.derivation.kind == "ontology_migration"
    assert candidate.derivation.run_id == first.migration_sha256
    assert candidate.migrates_assertion_ids == [source_assertion.id]
    with pytest.raises(
        ValidationError,
        match=r"ontology version must be core/1\.0\.0",
    ):
        container.application.knowledge.review_approve(
            context,
            candidate.id,
        )

    target_project = tmp_path / "target-project"
    target_project.mkdir()
    shutil.copytree(after, target_project / "ontology")
    activated = build_container(
        replace(container.settings, project_root=target_project),
        repository=container.repository,
    )
    target_assertion = activated.application.knowledge.review_approve(
        context,
        candidate.id,
    )
    assert container.application.knowledge.get_assertion(
        context,
        source_assertion.id,
    ).status == "active"
    assert [
        edge.assertion_id
        for edge in container.application.knowledge.graph_neighbors(
            context,
            GraphNeighborsRequest(node_id="doc_new", direction="out"),
        )
    ] == [source_assertion.id]
    assert [
        edge.assertion_id
        for edge in activated.application.knowledge.graph_neighbors(
            context,
            GraphNeighborsRequest(node_id="doc_new", direction="out"),
        )
    ] == [target_assertion.id]


def test_split_materializes_one_review_candidate_per_target(tmp_path: Path) -> None:
    container = _container(tmp_path)
    context, _, source_assertion = _seed_approved_assertion(container, tmp_path)
    before = _copy_release(tmp_path, "before")
    after = _copy_release(tmp_path, "after")
    targets = ("amends_delivery", "amends_payment")
    _rename_predicate(after, source="amends", targets=targets)

    summary = container.application.ontology_migrations.materialize(
        context,
        before,
        after,
        _migration(operation="split", targets=targets),
    )

    candidates = [
        container.application.knowledge.get_candidate(context, candidate_id)
        for candidate_id in summary.candidate_ids
    ]
    assert {candidate.predicate for candidate in candidates} == set(targets)
    assert all(candidate.review_risk == "high" for candidate in candidates)
    assert all(
        candidate.migrates_assertion_ids == [source_assertion.id]
        for candidate in candidates
    )
    assert container.application.knowledge.get_assertion(
        context,
        source_assertion.id,
    ).status == "active"


def test_existing_assertions_cannot_be_migrated_without_review(tmp_path: Path) -> None:
    container = _container(tmp_path)
    context, _, _ = _seed_approved_assertion(container, tmp_path)
    before = _copy_release(tmp_path, "before")
    after = _copy_release(tmp_path, "after")
    _rename_predicate(after, source="amends", targets=("revises",))

    with pytest.raises(
        ValidationError,
        match="existing assertions require review_required=true",
    ):
        container.application.ontology_migrations.materialize(
            context,
            before,
            after,
            _migration(
                operation="rename",
                targets=("revises",),
                review=False,
            ),
        )

    assert container.application.knowledge.list_candidates(context) == []


def test_materialization_is_acl_filtered_before_source_assertion_scan(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    _, _, _ = _seed_approved_assertion(container, tmp_path)
    before = _copy_release(tmp_path, "before")
    after = _copy_release(tmp_path, "after")
    _rename_predicate(after, source="amends", targets=("revises",))
    denied = RequestContext(
        workspace="default",
        principal_id="denied",
        acl_scopes=[],
        request_id=new_id("req"),
    )

    summary = container.application.ontology_migrations.materialize(
        denied,
        before,
        after,
        _migration(operation="rename", targets=("revises",)),
    )

    assert summary.source_assertion_count == 0
    assert summary.candidate_ids == []


def test_materialization_validates_every_target_before_persisting(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    context, _, _ = _seed_approved_assertion(container, tmp_path)
    before = _copy_release(tmp_path, "before")
    after = _copy_release(tmp_path, "after")
    targets = ("amends_delivery", "amends_person")
    _rename_predicate(after, source="amends", targets=targets)
    predicate_path = after / "core/predicates.yaml"
    payload = yaml.safe_load(predicate_path.read_text(encoding="utf-8"))
    payload["predicates"]["amends_person"]["range"] = ["Person"]
    predicate_path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="outside predicate amends_person range"):
        container.application.ontology_migrations.materialize(
            context,
            before,
            after,
            _migration(operation="split", targets=targets),
        )

    assert container.application.knowledge.list_candidates(context) == []


def test_migration_operation_rejects_ambiguous_split_arity() -> None:
    with pytest.raises(ValueError, match="at least two targets"):
        _migration(operation="split", targets=("revises",))
