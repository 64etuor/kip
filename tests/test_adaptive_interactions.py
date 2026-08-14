from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.interactions import (
    ClarificationAnswer,
    ClarificationRequest,
    FeedbackSubmission,
    OntologyDiscoveryProposal,
    OntologyDiscoveryReview,
)
from kip.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from kip.ontology import OntologyCatalog
from kip.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def _container(
    tmp_path: Path,
    *,
    enabled: bool = True,
    discovery: bool = True,
    project_root: Path | None = None,
):
    source_root = tmp_path / "source"
    source_root.mkdir()
    return build_container(
        Settings(
            project_root=project_root or ROOT,
            config_path=tmp_path / "kip.toml",
            raw={
                "search": {"semantic_enabled": False},
                "graph": {"backend": "memory"},
                "sources": {"filesystem": []},
                "interaction": {
                    "enabled": enabled,
                    "clarification_ttl_seconds": 60,
                },
                "ontology": {
                    "domain_profile": "empty",
                    "adaptive_discovery": discovery,
                },
            },
            environment="test",
            workspace="default",
            database_url="memory://",
            cas_path=tmp_path / "cas",
            api_key="test-key",
            admin_key="test-admin",
        ),
        repository=MemoryRepository(),
    )


def _copied_project_root(tmp_path: Path) -> Path:
    """A writable copy of the repo's `ontology/` tree.

    Approving an `entity_type`/`predicate` discovery candidate now
    materializes it into the ontology tree the running container was built
    from (see `kip.ontology_discovery_release`), so any test that exercises
    the accept path must never point `project_root` at the real repo
    checkout, or it would mutate tracked ontology files on disk.
    """
    project_root = tmp_path / "repo"
    shutil.copytree(ROOT / "ontology", project_root / "ontology")
    return project_root


def _context(container, principal: str = "principal_a", *, admin: bool = False):
    context = container.application.operations.request_context(
        principal_id=principal,
        acl_scopes=["workspace:default"],
    )
    return context.model_copy(update={"roles": ["admin"] if admin else []})


def test_clarification_remembers_only_a_confirmed_answer_for_its_owner(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    context = _context(container)
    now = datetime(2026, 8, 10, tzinfo=UTC)

    question = container.application.interactions.create_clarification(
        context,
        ClarificationRequest(
            reason="scope_selection",
            prompt="어느 문서 범위를 기본으로 검색할까요?",
            choices=[
                {"id": "onedrive", "label": "OneDrive"},
                {"id": "nas", "label": "NAS"},
            ],
            allow_freeform=False,
            preference_key="default_source_scope",
        ),
        now=now,
    )

    resolution = container.application.interactions.answer_clarification(
        context,
        ClarificationAnswer(
            question_id=question.id,
            option_ids=["onedrive"],
            remember=True,
        ),
        now=now + timedelta(seconds=1),
    )

    assert resolution.question.status == "answered"
    assert resolution.selected_values == ["onedrive"]
    assert resolution.preference is not None
    assert resolution.preference.key == "default_source_scope"
    assert resolution.preference.values == ["onedrive"]
    assert container.application.interactions.list_preferences(context) == [
        resolution.preference
    ]
    with pytest.raises(NotFoundError):
        container.application.interactions.get_clarification(
            _context(container, "principal_b"),
            question.id,
            now=now + timedelta(seconds=1),
        )


def test_expired_clarification_cannot_persist_a_preference(tmp_path: Path) -> None:
    container = _container(tmp_path)
    context = _context(container)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    question = container.application.interactions.create_clarification(
        context,
        ClarificationRequest(
            reason="preference",
            prompt="검색 결과 수를 기억할까요?",
            choices=[{"id": "ten", "label": "10"}],
            allow_freeform=False,
            preference_key="result_limit",
        ),
        now=now,
    )

    with pytest.raises(ConflictError, match="expired"):
        container.application.interactions.answer_clarification(
            context,
            ClarificationAnswer(
                question_id=question.id,
                option_ids=["ten"],
                remember=True,
            ),
            now=now + timedelta(seconds=61),
        )
    assert container.application.interactions.list_preferences(context) == []


def test_interaction_storage_is_disabled_without_explicit_configuration(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path, enabled=False)

    with pytest.raises(ValidationError, match="interaction memory is disabled"):
        container.application.interactions.create_clarification(
            _context(container),
            ClarificationRequest(
                reason="ambiguous_term",
                prompt="어떤 계약을 뜻하나요?",
            ),
        )


def test_feedback_is_structured_and_separate_from_query_traces(tmp_path: Path) -> None:
    container = _container(tmp_path)
    context = _context(container)
    feedback = container.application.interactions.submit_feedback(
        context,
        FeedbackSubmission(
            request_id="req_" + "a" * 32,
            outcome="not_helpful",
            reason_codes=["wrong_scope", "missing_evidence"],
        ),
        now=datetime(2026, 8, 10, tzinfo=UTC),
    )

    assert feedback.outcome == "not_helpful"
    assert feedback.reason_codes == ["wrong_scope", "missing_evidence"]
    assert container.repository.state.query_traces == []
    assert "query" not in feedback.model_dump(mode="json")


def test_discovery_candidates_are_deduplicated_and_proposing_never_auto_activates(
    tmp_path: Path,
) -> None:
    project_root = _copied_project_root(tmp_path)
    ontology_root = project_root / "ontology"
    container = _container(tmp_path, project_root=project_root)
    context = _context(container)
    proposal = OntologyDiscoveryProposal(
        kind="entity_type",
        symbol="contract",
        label="계약",
        definition="업무상 체결하는 계약을 표현한다.",
        confirmed=True,
    )

    first = container.application.interactions.propose_ontology_discovery(
        context,
        proposal,
        now=datetime(2026, 8, 10, tzinfo=UTC),
    )
    second = container.application.interactions.propose_ontology_discovery(
        context,
        proposal,
        now=datetime(2026, 8, 10, 0, 1, tzinfo=UTC),
    )

    assert second.id == first.id
    assert second.occurrence_count == 2
    with pytest.raises(AuthorizationError, match="admin role"):
        container.application.interactions.list_ontology_discovery_candidates(
            context
        )
    # Merely proposing (never reviewed) must never touch the ontology tree.
    with pytest.raises(ValidationError, match="unknown ontology entity type"):
        OntologyCatalog.load(ontology_root, domain_profile="empty").validate_entity_type(
            "contract"
        )

    reviewed = container.application.interactions.review_ontology_discovery_candidate(
        _context(container, admin=True),
        first.id,
        OntologyDiscoveryReview(action="accept"),
        now=datetime(2026, 8, 10, 0, 2, tzinfo=UTC),
    )

    # Approval materializes the release onto disk immediately...
    assert reviewed.status == "accepted_for_release"
    assert reviewed.release is not None
    assert reviewed.release.kind == "entity_type"
    assert reviewed.release.symbol == "contract"
    assert reviewed.release.file == "domains/empty.yaml"
    assert reviewed.release.version == "1.1.0"
    assert reviewed.release.catalog_refresh == "restart_required"
    OntologyCatalog.load(ontology_root, domain_profile="empty").validate_entity_type("contract")
    # ...but the catalog snapshot this already-running container holds is not
    # hot-swapped in place; a fresh load (a new process, or a restart) is
    # required to see it, matching `catalog_refresh: "restart_required"`.
    with pytest.raises(ValidationError, match="unknown ontology entity type"):
        container.ontology.validate_entity_type("contract")


def test_discovery_requires_its_own_explicit_opt_in(tmp_path: Path) -> None:
    container = _container(tmp_path, discovery=False)

    with pytest.raises(ValidationError, match="ontology discovery is disabled"):
        container.application.interactions.propose_ontology_discovery(
            _context(container),
            OntologyDiscoveryProposal(
                kind="entity_type",
                symbol="contract",
                label="계약",
                definition="업무상 체결하는 계약을 표현한다.",
                confirmed=True,
            ),
        )


def test_approving_a_predicate_candidate_materializes_defaults_and_syncs_review_policy(
    tmp_path: Path,
) -> None:
    project_root = _copied_project_root(tmp_path)
    ontology_root = project_root / "ontology"
    container = _container(tmp_path, project_root=project_root)
    context = _context(container)
    proposed = container.application.interactions.propose_ontology_discovery(
        context,
        OntologyDiscoveryProposal(
            kind="predicate",
            symbol="cites",
            label="인용",
            definition="한 문서가 다른 문서를 인용한다.",
            confirmed=True,
        ),
        now=datetime(2026, 8, 10, tzinfo=UTC),
    )

    reviewed = container.application.interactions.review_ontology_discovery_candidate(
        _context(container, admin=True),
        proposed.id,
        OntologyDiscoveryReview(action="accept"),
        now=datetime(2026, 8, 10, 0, 1, tzinfo=UTC),
    )

    assert reviewed.release is not None
    assert reviewed.release.kind == "predicate"
    assert reviewed.release.file == "core/predicates.yaml"
    catalog = OntologyCatalog.load(ontology_root, domain_profile="empty")
    assert "cites" in catalog.evidence_required_predicates()
    assert catalog.predicate_specs["cites"].domain == ("EvidenceObject",)


def test_approval_materializes_before_persisting_status_and_never_activates_on_reject(
    tmp_path: Path,
) -> None:
    project_root = _copied_project_root(tmp_path)
    container = _container(tmp_path, project_root=project_root)
    context = _context(container)
    proposed = container.application.interactions.propose_ontology_discovery(
        context,
        OntologyDiscoveryProposal(
            kind="entity_type",
            symbol="contract",
            label="계약",
            definition="업무상 체결하는 계약을 표현한다.",
            confirmed=True,
        ),
        now=datetime(2026, 8, 10, tzinfo=UTC),
    )

    rejected = container.application.interactions.review_ontology_discovery_candidate(
        _context(container, admin=True),
        proposed.id,
        OntologyDiscoveryReview(action="reject"),
        now=datetime(2026, 8, 10, 0, 1, tzinfo=UTC),
    )

    assert rejected.status == "rejected"
    assert rejected.release is None
    with pytest.raises(ValidationError, match="unknown ontology entity type"):
        OntologyCatalog.load(
            project_root / "ontology", domain_profile="empty"
        ).validate_entity_type("contract")


def test_approval_that_fails_to_materialize_leaves_the_candidate_proposed(
    tmp_path: Path,
) -> None:
    project_root = _copied_project_root(tmp_path)
    container = _container(tmp_path, project_root=project_root)
    context = _context(container)
    # `parent` is explicit, so an unknown parent fails shadow validation
    # rather than silently falling back to a root type.
    proposed = container.application.interactions.propose_ontology_discovery(
        context,
        OntologyDiscoveryProposal(
            kind="entity_type",
            symbol="contract",
            label="계약",
            definition="업무상 체결하는 계약을 표현한다.",
            parent="no_such_entity_type",
            confirmed=True,
        ),
        now=datetime(2026, 8, 10, tzinfo=UTC),
    )
    admin_context = _context(container, admin=True)

    with pytest.raises(ValidationError, match="shadow validation"):
        container.application.interactions.review_ontology_discovery_candidate(
            admin_context,
            proposed.id,
            OntologyDiscoveryReview(action="accept"),
            now=datetime(2026, 8, 10, 0, 1, tzinfo=UTC),
        )

    still_proposed = container.application.interactions.list_ontology_discovery_candidates(
        admin_context,
        status="proposed",
    )
    assert [candidate.id for candidate in still_proposed] == [proposed.id]
    with pytest.raises(ValidationError, match="unknown ontology entity type"):
        OntologyCatalog.load(
            project_root / "ontology", domain_profile="empty"
        ).validate_entity_type("contract")
