from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from kip.adapters.repository.postgres import PostgresRepository
from kip.container import build_container
from kip.domain.egress import DataClassification
from kip.domain.identity import AclSnapshot
from kip.domain.interactions import (
    ClarificationAnswer,
    ClarificationRequest,
    OntologyDiscoveryProposal,
    OntologyDiscoveryReview,
)
from kip.domain.knowledge import (
    CandidateEvidence,
    EntityCandidate,
    KnowledgeEntity,
    MinedEntityProposal,
    RelationDerivation,
    RelationProposal,
    entity_candidate_fingerprint,
    stable_entity_candidate_id,
)
from kip.domain.models import (
    ContentUnit,
    DocumentPacket,
    EmbeddingRecord,
    EmbeddingSpace,
    ExtractionRun,
    GraphNeighborsRequest,
    GraphPathRequest,
    RequestContext,
    SearchRequest,
)
from kip.domain.telemetry import QueryTrace
from kip.errors import NotFoundError, ValidationError
from kip.ids import new_id, stable_id
from kip.ontology_migration import OntologyMigration
from kip.settings import Settings

URL = os.environ.get("KIP_TEST_POSTGRES_URL") or os.environ.get("KIP_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="PostgreSQL integration URL not configured")


def test_postgres_interactions_enforce_owner_scope_and_review_lifecycle(
    tmp_path: Path,
) -> None:
    pytest.importorskip("psycopg")
    workspace = "test_" + uuid.uuid4().hex[:12]
    settings = Settings(
        project_root=Path(__file__).resolve().parents[2],
        config_path=tmp_path / "kip.toml",
        raw={
            "search": {"semantic_enabled": False},
            "graph": {"backend": "memory"},
            "sources": {"filesystem": []},
            "interaction": {
                "enabled": True,
                "clarification_ttl_seconds": 3600,
            },
            "ontology": {
                "domain_profile": "empty",
                "adaptive_discovery": True,
            },
        },
        environment="test",
        workspace=workspace,
        database_url=str(URL),
        cas_path=tmp_path / "cas",
    )
    repository = PostgresRepository(str(URL))
    container = build_container(settings, repository=repository)
    repository.operations.migrate(settings.project_root / "migrations")
    owner = container.application.operations.request_context(
        workspace=workspace,
        principal_id="principal_owner",
        acl_scopes=[f"workspace:{workspace}"],
    )
    other = owner.model_copy(update={"principal_id": "principal_other"})

    try:
        question = container.application.interactions.create_clarification(
            owner,
            ClarificationRequest(
                reason="scope_selection",
                prompt="어느 범위를 검색할까요?",
                choices=[{"id": "onedrive", "label": "OneDrive"}],
                allow_freeform=False,
                preference_key="default_source_scope",
            ),
        )
        resolution = container.application.interactions.answer_clarification(
            owner,
            ClarificationAnswer(
                question_id=question.id,
                option_ids=["onedrive"],
                remember=True,
            ),
        )

        assert resolution.preference is not None
        assert container.application.interactions.list_preferences(owner)[0].values == [
            "onedrive"
        ]
        with pytest.raises(NotFoundError):
            container.application.interactions.get_clarification(other, question.id)

        proposal = OntologyDiscoveryProposal(
            kind="entity_type",
            symbol="contract",
            label="계약",
            definition="업무상 체결하는 계약을 표현한다.",
            confirmed=True,
        )
        first = container.application.interactions.propose_ontology_discovery(
            owner,
            proposal,
        )
        duplicate = container.application.interactions.propose_ontology_discovery(
            owner,
            proposal,
        )
        reviewed = container.application.interactions.review_ontology_discovery_candidate(
            owner.model_copy(update={"roles": ["admin"]}),
            first.id,
            OntologyDiscoveryReview(action="accept"),
        )

        assert duplicate.id == first.id
        assert duplicate.occurrence_count == 2
        assert reviewed.status == "accepted_for_release"
    finally:
        import psycopg

        with (
            psycopg.connect(str(URL), autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("DELETE FROM kip.workspaces WHERE slug=%s", (workspace,))


def test_postgres_replacing_extraction_swaps_lexical_projection(
    tmp_path: Path,
) -> None:
    # Given one active extraction for a current source revision.
    pytest.importorskip("psycopg")
    workspace = "test_" + uuid.uuid4().hex[:12]
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "evidence.txt").write_text("기존 검색 근거", encoding="utf-8")
    settings = Settings(
        project_root=Path(__file__).resolve().parents[2],
        config_path=tmp_path / "kip.toml",
        raw={
            "database": {"statement_timeout_ms": 15000},
            "search": {"korean_ngram_min": 2, "korean_ngram_max": 4},
            "sources": {
                "filesystem": [
                    {
                        "name": "fixture",
                        "root": str(source_root),
                        "enabled": True,
                        "settle_seconds": 0,
                        "include_extensions": [".txt"],
                        "acl_scope": f"workspace:{workspace}",
                    }
                ]
            },
            "parsers": {"hwp": {"order": ["paired_pdf"]}},
        },
        environment="test",
        workspace=workspace,
        database_url=str(URL),
        cas_path=tmp_path / "cas",
    )
    repository = PostgresRepository(str(URL))
    container = build_container(settings, repository=repository)
    repository.operations.migrate(settings.project_root / "migrations")
    context = container.application.operations.request_context(
        workspace=workspace,
        acl_scopes=[f"workspace:{workspace}"],
    )

    try:
        container.application.ingestion.sync_filesystem(context, "fixture")
        original_hit = container.application.retrieval.search(
            context,
            SearchRequest(query="기존"),
        )[0]
        original_unit = repository.evidence.get_content_unit(
            context,
            original_hit.unit_id,
        )
        view = repository.evidence.get_artifact(context, original_hit.artifact_id)
        assert view.source_object is not None
        assert view.revision is not None
        assert view.document is not None
        extraction_id = new_id("ext")
        candidate_unit = ContentUnit(
            id=stable_id("unit", extraction_id, "0"),
            extraction_id=extraction_id,
            document_id=original_unit.document_id,
            artifact_id=original_unit.artifact_id,
            ordinal=0,
            unit_type="replacement",
            title=original_unit.title,
            body="교체된 검색 근거",
            body_normalized="교체된 검색 근거",
            lexical_text="교체된 검색 근거 교체 검색 근거",
            locator=original_unit.locator,
            classification=original_unit.classification,
            acl_scopes=original_unit.acl_scopes,
            acl_snapshot_id=original_unit.acl_snapshot_id,
        )
        candidate = DocumentPacket(
            workspace_id=workspace,
            source_object=view.source_object,
            revision=view.revision,
            logical_document=view.document,
            artifact=view.artifact,
            extraction=ExtractionRun(
                id=extraction_id,
                artifact_id=view.artifact.id,
                parser_name="replacement-parser",
                parser_version="2.0",
                status="succeeded",
                quality_score=0.95,
                output_hash="d" * 64,
            ),
            units=[candidate_unit],
        )

        # When the candidate is activated through the repository port.
        result = repository.ingestion.replace_extraction(context, candidate)

        # Then search uses only the new unit while the old unit remains auditable.
        replacement_hits = container.application.retrieval.search(
            context,
            SearchRequest(query="교체된"),
        )
        assert result.status == "replaced"
        assert replacement_hits[0].unit_id == candidate_unit.id
        assert (
            container.application.retrieval.search(
                context,
                SearchRequest(query="기존"),
            )
            == []
        )
        assert (
            repository.evidence.get_content_unit(
                context,
                original_unit.id,
            ).body
            == "기존 검색 근거"
        )
        assert repository.operations.status(context).active_extractions == 1
    finally:
        import psycopg

        with (
            psycopg.connect(str(URL), autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("DELETE FROM kip.workspaces WHERE slug=%s", (workspace,))


def _predicate_rename_release(tmp_path: Path) -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[2]
    before = tmp_path / "ontology-before"
    after = tmp_path / "ontology-after"
    shutil.copytree(root / "ontology", before)
    shutil.copytree(root / "ontology", after)
    predicate_path = after / "core/predicates.yaml"
    predicate_payload = yaml.safe_load(predicate_path.read_text(encoding="utf-8"))
    definition = predicate_payload["predicates"].pop("amends")
    predicate_payload["predicates"]["revises"] = definition
    predicate_payload["version"] = "2.0.0"
    predicate_path.write_text(
        yaml.safe_dump(predicate_payload, sort_keys=False),
        encoding="utf-8",
    )
    policy_path = after / "policies/review-policy.yaml"
    policy_payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    required = policy_payload["human_review_required"]["predicates"]
    policy_payload["human_review_required"]["predicates"] = [
        "revises" if item == "amends" else item for item in required
    ]
    policy_path.write_text(
        yaml.safe_dump(policy_payload, sort_keys=False),
        encoding="utf-8",
    )
    return before, after


def test_postgres_migrate_ingest_search_and_status(tmp_path: Path):
    pytest.importorskip("psycopg")
    workspace = "test_" + uuid.uuid4().hex[:12]
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "공문.txt").write_text(
        "A과제 참여율 변경은 2026년 7월 1일부터 승인한다.",
        encoding="utf-8",
    )
    settings = Settings(
        project_root=Path(__file__).resolve().parents[2],
        config_path=tmp_path / "kip.toml",
        raw={
            "database": {"statement_timeout_ms": 15000},
            "search": {"korean_ngram_min": 2, "korean_ngram_max": 4},
            "sources": {
                "filesystem": [
                    {
                        "name": "fixture",
                        "root": str(source_root),
                        "enabled": True,
                        "settle_seconds": 0,
                            "include_extensions": [".txt"],
                            "acl_scope": f"workspace:{workspace}",
                            "classification": "internal",
                    }
                ]
            },
            "parsers": {"hwp": {"order": ["paired_pdf"]}},
        },
        environment="test",
        workspace=workspace,
        database_url=str(URL),
        cas_path=tmp_path / "cas",
    )
    repository = PostgresRepository(str(URL))
    container = build_container(settings, repository=repository)
    repository.operations.migrate(settings.project_root / "migrations")
    context = container.application.operations.request_context(
        workspace=workspace,
        acl_scopes=[f"workspace:{workspace}"],
    )
    try:
        summary = container.application.ingestion.sync_filesystem(context, "fixture")
        assert summary.inserted == 1
        hits = container.application.retrieval.search(context, SearchRequest(query="참여율 변경 승인", limit=10))
        assert hits
        bulk_units = repository.evidence.get_content_units(
            context,
            [hit.unit_id for hit in hits],
        )
        assert [unit.id for unit in bulk_units] == [hit.unit_id for hit in hits]
        natural_hits = container.application.retrieval.search(
            context,
            SearchRequest(
                query="A과제에서 참여 비율을 바꾸는 내용은 언제부터 허가된다고 적혀 있는가?",
                limit=10,
            ),
        )
        assert natural_hits
        assert natural_hits[0].document_id == hits[0].document_id
        ngram_hits = container.application.retrieval.search(
            context,
            SearchRequest(query="참여 비율", limit=10),
        )
        assert ngram_hits
        rebuild = container.application.operations.rebuild_projection(context, "lexical")
        assert rebuild["changed_units"] == 0
        assert rebuild["deleted_units"] == 0
        rebuilt_ngram_hits = container.application.retrieval.search(
            context,
            SearchRequest(query="참여 비율", limit=10),
        )
        assert [hit.unit_id for hit in rebuilt_ngram_hits] == [
            hit.unit_id for hit in ngram_hits
        ]
        traces = container.application.telemetry.list_traces(
            context.model_copy(update={"roles": ["admin"]})
        )
        assert traces[0].route == "search"
        assert traces[0].candidates[0].unit_id == natural_hits[0].unit_id
        assert "A과제에서 참여 비율" not in traces[0].model_dump_json()
        repository.telemetry.record(
            context,
            QueryTrace(
                route="search",
                outcome="succeeded",
                started_at=datetime.now(UTC) - timedelta(days=31),
                duration_ms=1,
            ),
        )
        assert container.application.telemetry.prune(
            context.model_copy(update={"roles": ["admin"]})
        ) == 1
        evidence = container.application.evidence.read_unit(context, hits[0].unit_id)
        assert evidence.source_changed_since_index is False

        embeddable = repository.retrieval.list_embeddable_units(context)
        space = EmbeddingSpace(
            id=stable_id("espace", workspace, "fixture-1024"),
            name="fixture-1024",
            provider="fixture",
            model="fixture",
            revision="fixture-v1",
            dimensions=1024,
            normalized=True,
            status="shadow",
        )
        repository.retrieval.save_embedding_space(context, space)
        repository.retrieval.upsert_embeddings(
            context,
            space.id,
            [
                EmbeddingRecord(
                    unit_id=embeddable[0].unit_id,
                    embedding=[1.0] + [0.0] * 1023,
                    source_hash=embeddable[0].source_hash,
                )
            ],
        )
        repository.retrieval.activate_embedding_space(context, space.id)
        vector_hits = repository.retrieval.vector_search(
            context,
            SearchRequest(query="표현이 다른 승인 질의", limit=10),
            [1.0] + [0.0] * 1023,
            space_id=space.id,
            limit=10,
        )
        assert vector_hits[0].unit_id == hits[0].unit_id

        for entity in (
            KnowledgeEntity(
                id="doc_new",
                entity_type="Document",
                canonical_name="변경 공문",
                aliases=["변경승인공문"],
                acl_scopes=[f"workspace:{workspace}"],
            ),
            KnowledgeEntity(
                id="doc_old",
                entity_type="Document",
                canonical_name="원 협약",
                acl_scopes=[f"workspace:{workspace}"],
            ),
        ):
            container.application.ontology_rag.create_entity(context, entity)
        assert [
            entity.id
            for entity in repository.knowledge.resolve_entities(
                context, "변경승인공문", limit=8
            )
        ] == ["doc_new"]
        proposal = RelationProposal(
            subject_id="doc_new",
            predicate="amends",
            object_entity_id="doc_old",
            ontology_version="core/1.0.0",
            evidence_unit_ids=(hits[0].unit_id,),
            derivation=RelationDerivation(
                kind="model",
                name="postgres-integration",
                model="fixture",
                revision="r1",
            ),
        )
        candidate = container.application.ontology_rag.propose_relation(
            context,
            proposal,
        )
        duplicate = container.application.ontology_rag.propose_relation(
            context,
            proposal,
        )
        assert duplicate.id == candidate.id
        assert repository.knowledge.get_entity(context, "doc_new").entity_type == "Document"
        assertion = container.application.knowledge.review_approve(context, candidate.id)
        explanation = container.application.knowledge.explain_assertion(context, assertion.id)
        assert explanation.assertion.id == assertion.id
        assert explanation.evidence[0].unit.id == hits[0].unit_id
        before_release, after_release = _predicate_rename_release(tmp_path)
        ontology_migration = OntologyMigration.model_validate(
            {
                "schema_version": "kip.ontology-migration.v1",
                "from_version": "core/1.0.0",
                "to_version": "core/2.0.0",
                "operations": [
                    {
                        "operation": "rename",
                        "symbol_kind": "predicate",
                        "sources": ["amends"],
                        "targets": ["revises"],
                        "review_required": True,
                    }
                ],
            }
        )
        materialized = container.application.ontology_migrations.materialize(
            context,
            before_release,
            after_release,
            ontology_migration,
        )
        repeated_materialization = (
            container.application.ontology_migrations.materialize(
                context,
                before_release,
                after_release,
                ontology_migration,
            )
        )
        assert materialized.created_candidate_count == 1
        assert repeated_materialization.created_candidate_count == 0
        assert repeated_materialization.existing_candidate_count == 1
        migration_candidate = repository.knowledge.get_candidate(
            context,
            materialized.candidate_ids[0],
        )
        assert migration_candidate.predicate == "revises"
        assert migration_candidate.migrates_assertion_ids == [assertion.id]
        with pytest.raises(
            ValidationError,
            match=r"ontology version must be core/1\.0\.0",
        ):
            container.application.knowledge.review_approve(
                context,
                migration_candidate.id,
            )
        assert repository.knowledge.get_assertion(context, assertion.id).status == "active"
        edges = repository.knowledge.graph_neighbors(
            context,
            GraphNeighborsRequest(node_id="doc_new", direction="out"),
        )
        assert [edge.assertion_id for edge in edges] == [assertion.id]
        assert repository.knowledge.graph_neighbors(
            context,
            GraphNeighborsRequest(node_id="doc_new", direction="out"),
            ontology_version="core/2.0.0",
        ) == []
        paths = repository.knowledge.graph_path(
            context,
            GraphPathRequest(from_node_id="doc_new", to_node_id="doc_old"),
        )
        assert [path.assertion_ids for path in paths] == [[assertion.id]]
        assert repository.knowledge.graph_path(
            context,
            GraphPathRequest(from_node_id="doc_new", to_node_id="doc_old"),
            ontology_version="core/2.0.0",
        ) == []
        container.application.ontology_rag.create_entity(
            context,
            KnowledgeEntity(
                id="doc_expired",
                entity_type="Document",
                canonical_name="효력 만료 협약",
                acl_scopes=[f"workspace:{workspace}"],
            ),
        )
        validity_now = datetime.now(UTC)
        expired_candidate = container.application.ontology_rag.propose_relation(
            context,
            RelationProposal(
                subject_id="doc_new",
                predicate="amends",
                object_entity_id="doc_expired",
                ontology_version="core/1.0.0",
                evidence_unit_ids=(hits[0].unit_id,),
                valid_from=validity_now - timedelta(days=2),
                valid_to=validity_now - timedelta(days=1),
                derivation=RelationDerivation(
                    kind="manual",
                    name="postgres-temporal-integration",
                    revision="expired-v1",
                ),
            ),
        )
        expired_assertion = container.application.knowledge.review_approve(
            context,
            expired_candidate.id,
        )
        current_edges = repository.knowledge.graph_neighbors(
            context,
            GraphNeighborsRequest(node_id="doc_new", direction="out"),
        )
        assert expired_assertion.id not in {
            edge.assertion_id for edge in current_edges
        }
        assert repository.knowledge.graph_path(
            context,
            GraphPathRequest(
                from_node_id="doc_new",
                to_node_id="doc_expired",
            ),
        ) == []

        entity_derivation = RelationDerivation(
            kind="model",
            name="postgres-integration",
            model="fixture",
            revision="r1",
        )
        entity_evidence = CandidateEvidence(
            content_unit_id=hits[0].unit_id,
            source_revision_sha256=evidence.indexed_source_sha256,
            locator=evidence.unit.locator.model_dump(mode="json"),
            quote_hash="sha256:"
            + hashlib.sha256(evidence.unit.body.encode()).hexdigest(),
        )
        entity_proposal = MinedEntityProposal(
            entity_type="Project",
            canonical_name="PostgreSQL 통합 과제",
            aliases=["PG 통합 과제"],
            evidence_ids=(hits[0].unit_id,),
            confidence=0.9,
        )
        entity_fingerprint = entity_candidate_fingerprint(
            entity_proposal,
            ontology_version="core/1.0.0",
            evidence=(entity_evidence,),
            derivation=entity_derivation,
        )
        entity_candidate = EntityCandidate(
            id=stable_entity_candidate_id(entity_fingerprint),
            fingerprint=entity_fingerprint,
            entity_type=entity_proposal.entity_type,
            canonical_name=entity_proposal.canonical_name,
            aliases=entity_proposal.aliases,
            origin="model:postgres-integration",
            confidence=entity_proposal.confidence,
            ontology_version="core/1.0.0",
            evidence=[entity_evidence],
            derivation=entity_derivation,
        )
        stored_entity_candidate = repository.knowledge.save_entity_candidate(
            context,
            entity_candidate,
        )
        duplicate_entity_candidate = repository.knowledge.save_entity_candidate(
            context,
            entity_candidate,
        )
        hidden_context = RequestContext(
            workspace=workspace,
            principal_id="principal_hidden_candidate",
            acl_scopes=[],
            request_id=new_id("req"),
        )
        assert duplicate_entity_candidate.id == stored_entity_candidate.id
        assert repository.knowledge.list_entity_candidates(hidden_context) == []
        assert repository.knowledge.list_candidates(
            hidden_context,
            status="approved",
        ) == []
        assert repository.knowledge.list_entities(hidden_context) == []
        assert (
            repository.knowledge.resolve_entities(
                hidden_context, "변경승인공문", limit=8
            )
            == []
        )
        with pytest.raises(NotFoundError):
            repository.knowledge.get_entity_candidate(
                hidden_context,
                stored_entity_candidate.id,
            )
        with pytest.raises(NotFoundError):
            repository.knowledge.get_candidate(hidden_context, candidate.id)
        with pytest.raises(NotFoundError):
            repository.knowledge.get_entity(hidden_context, "doc_new")
        approved_entity = repository.knowledge.approve_entity_candidate(
            context,
            stored_entity_candidate.id,
            context.principal_id,
        )
        assert approved_entity.canonical_name == "PostgreSQL 통합 과제"
        assert approved_entity.aliases == ["PG 통합 과제"]
        assert approved_entity.acl_scopes == [f"workspace:{workspace}"]

        source_object = repository.evidence.get_artifact(
            context,
            hits[0].artifact_id,
        ).source_object
        assert source_object is not None
        assert source_object.classification is DataClassification.INTERNAL
        assert repository.evidence.get_content_unit(
            context,
            hits[0].unit_id,
        ).classification is DataClassification.INTERNAL
        now = datetime.now(UTC)
        stale_snapshot = AclSnapshot(
            id=new_id("aclsnap"),
            version="directory-stale",
            provider="integration-directory",
            scopes=[f"workspace:{workspace}"],
            captured_at=now - timedelta(hours=2),
            expires_at=now - timedelta(minutes=1),
        )
        repository.ingestion.upsert_acl_snapshot(
            context,
            source_object.id,
            stale_snapshot,
            source_object.classification,
        )
        assert container.application.retrieval.search(
            context,
            SearchRequest(query="참여율 변경 승인", limit=10),
        ) == []
        with pytest.raises(NotFoundError):
            repository.evidence.get_content_unit(context, hits[0].unit_id)
        with pytest.raises(NotFoundError):
            repository.knowledge.get_assertion(context, assertion.id)
        assert repository.knowledge.graph_neighbors(
            context,
            GraphNeighborsRequest(node_id="doc_new"),
        ) == []

        fresh_snapshot = AclSnapshot(
            id=new_id("aclsnap"),
            version="directory-fresh",
            provider="integration-directory",
            scopes=[f"workspace:{workspace}"],
            captured_at=now,
            expires_at=now + timedelta(minutes=15),
        )
        repository.ingestion.upsert_acl_snapshot(
            context,
            source_object.id,
            fresh_snapshot,
            source_object.classification,
        )
        assert container.application.retrieval.search(
            context,
            SearchRequest(query="참여율 변경 승인", limit=10),
        )
        assert repository.knowledge.graph_neighbors(
            context,
            GraphNeighborsRequest(node_id="doc_new"),
        )

        restricted_context = RequestContext(
            workspace=workspace,
            principal_id="principal_restricted",
            acl_scopes=[],
            request_id=new_id("req"),
        )
        with pytest.raises(NotFoundError):
            repository.knowledge.get_assertion(restricted_context, assertion.id)
        assert repository.knowledge.graph_neighbors(
            restricted_context,
            GraphNeighborsRequest(node_id="doc_new"),
        ) == []
        assert repository.knowledge.graph_path(
            restricted_context,
            GraphPathRequest(from_node_id="doc_new", to_node_id="doc_old"),
        ) == []

        status = repository.operations.status(context)
        assert status.source_objects == 1
        assert status.content_units == 1
    finally:
        import psycopg

        with (
            psycopg.connect(str(URL), autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("DELETE FROM kip.workspaces WHERE slug=%s", (workspace,))
