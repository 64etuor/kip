from __future__ import annotations

from pathlib import Path

import pytest

from kip.adapters.connectors.registry import ConfiguredSourceCatalog
from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.storage import LocalContentAddressedStore
from kip.application.analyzer import KoreanNgramAnalyzer
from kip.application.egress import EgressPolicyUseCases
from kip.application.ingestion_events import EventIngestionWorkflow
from kip.container import build_container
from kip.domain.egress import (
    ClassifiedEvidence,
    DataClassification,
    EgressDenialReason,
    EgressPolicy,
    EgressProvider,
    RetentionPolicy,
    evaluate_egress,
)
from kip.domain.models import ConnectorEvent, ContentUnit, EvidenceLocator, RequestContext
from kip.settings import Settings

CLASSIFICATIONS = tuple(DataClassification)
REMOTE_PROVIDERS = (EgressProvider.OPENAI, EgressProvider.ANTHROPIC)


def _evidence(
    classification: DataClassification | None,
    *,
    evidence_id: str = "unit_1",
) -> ClassifiedEvidence:
    return ClassifiedEvidence(id=evidence_id, classification=classification)


def _policy(
    provider: EgressProvider | None,
    *,
    allowed: tuple[DataClassification, ...] = CLASSIFICATIONS,
    retention: RetentionPolicy | None = RetentionPolicy.ZERO_RETENTION,
    secret_reference: str | None = "env:KIP_MODEL_API_KEY",
    allow_remote: bool = True,
) -> EgressPolicy:
    return EgressPolicy(
        enabled=True,
        provider=provider,
        allow_remote=allow_remote,
        allowed_classifications=allowed,
        retention_policy=retention,
        secret_reference=secret_reference,
        base_url="http://127.0.0.1:7998"
        if provider is EgressProvider.LOCAL
        else None,
    )


@pytest.mark.parametrize("classification", CLASSIFICATIONS)
def test_local_generation_admits_every_canonical_classification(
    classification: DataClassification,
) -> None:
    decision = evaluate_egress(
        _policy(
            EgressProvider.LOCAL,
            retention=None,
            secret_reference=None,
        ),
        [_evidence(classification)],
    )

    assert decision.allowed is True
    assert decision.allowed_evidence_ids == ("unit_1",)
    assert decision.denied_evidence_ids == ()
    assert decision.denial_reason is None


@pytest.mark.parametrize("provider", REMOTE_PROVIDERS)
@pytest.mark.parametrize("classification", CLASSIFICATIONS)
def test_remote_provider_requires_explicit_classification_and_zero_retention(
    provider: EgressProvider,
    classification: DataClassification,
) -> None:
    decision = evaluate_egress(
        _policy(provider, allowed=(classification,)),
        [_evidence(classification)],
    )

    assert decision.allowed is True
    assert decision.allowed_evidence_ids == ("unit_1",)


@pytest.mark.parametrize("provider", REMOTE_PROVIDERS)
def test_remote_public_evidence_may_use_explicit_provider_retention(
    provider: EgressProvider,
) -> None:
    decision = evaluate_egress(
        _policy(
            provider,
            allowed=(DataClassification.PUBLIC,),
            retention=RetentionPolicy.PROVIDER_DEFAULT,
        ),
        [_evidence(DataClassification.PUBLIC)],
    )

    assert decision.allowed is True


@pytest.mark.parametrize("provider", REMOTE_PROVIDERS)
def test_remote_non_public_evidence_denies_provider_default_retention(
    provider: EgressProvider,
) -> None:
    decision = evaluate_egress(
        _policy(
            provider,
            allowed=(DataClassification.INTERNAL,),
            retention=RetentionPolicy.PROVIDER_DEFAULT,
        ),
        [_evidence(DataClassification.INTERNAL)],
    )

    assert decision.allowed is False
    assert decision.denial_reason is EgressDenialReason.RETENTION_NOT_ALLOWED
    assert decision.allowed_evidence_ids == ()
    assert decision.denied_evidence_ids == ("unit_1",)


@pytest.mark.parametrize(
    ("policy", "evidence", "reason"),
    [
        (
            _policy(None),
            [_evidence(DataClassification.PUBLIC)],
            EgressDenialReason.MISSING_PROVIDER,
        ),
        (
            _policy(EgressProvider.OPENAI),
            [_evidence(None)],
            EgressDenialReason.MISSING_CLASSIFICATION,
        ),
        (
            _policy(EgressProvider.OPENAI, retention=None),
            [_evidence(DataClassification.PUBLIC)],
            EgressDenialReason.MISSING_RETENTION_POLICY,
        ),
        (
            _policy(EgressProvider.ANTHROPIC, secret_reference=None),
            [_evidence(DataClassification.PUBLIC)],
            EgressDenialReason.MISSING_SECRET_REFERENCE,
        ),
        (
            _policy(EgressProvider.OPENAI, allow_remote=False),
            [_evidence(DataClassification.PUBLIC)],
            EgressDenialReason.REMOTE_EGRESS_DISABLED,
        ),
    ],
)
def test_egress_policy_fails_closed_for_missing_controls(
    policy: EgressPolicy,
    evidence: list[ClassifiedEvidence],
    reason: EgressDenialReason,
) -> None:
    decision = evaluate_egress(policy, evidence)

    assert decision.allowed is False
    assert decision.denial_reason is reason
    assert decision.allowed_evidence_ids == ()
    assert decision.denied_evidence_ids == tuple(item.id for item in evidence)


def test_mixed_batch_reports_partial_policy_match_but_denies_generation() -> None:
    decision = evaluate_egress(
        _policy(
            EgressProvider.OPENAI,
            allowed=(DataClassification.PUBLIC,),
        ),
        [
            _evidence(DataClassification.PUBLIC, evidence_id="unit_public"),
            _evidence(DataClassification.RESTRICTED, evidence_id="unit_restricted"),
        ],
    )

    assert decision.allowed is False
    assert decision.denial_reason is EgressDenialReason.CLASSIFICATION_NOT_ALLOWED
    assert decision.allowed_evidence_ids == ("unit_public",)
    assert decision.denied_evidence_ids == ("unit_restricted",)


def test_application_policy_reads_classification_from_canonical_units() -> None:
    policy = EgressPolicyUseCases(
        _policy(
            EgressProvider.OPENAI,
            allowed=(DataClassification.PUBLIC,),
        )
    )
    unit = ContentUnit(
        id="unit_confidential",
        extraction_id="ext_1",
        artifact_id="art_1",
        ordinal=0,
        unit_type="paragraph",
        body="classified evidence",
        body_normalized="classified evidence",
        lexical_text="classified evidence",
        locator=EvidenceLocator(type="page", data={"page": 1}),
        classification=DataClassification.CONFIDENTIAL,
    )

    decision = policy.decide([unit])
    assert decision.allowed is False
    assert decision.denial_reason is EgressDenialReason.CLASSIFICATION_NOT_ALLOWED
    assert decision.denied_evidence_ids == ("unit_confidential",)


def test_connector_payload_cannot_downgrade_configured_classification(
    tmp_path: Path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "sources": {
                "connector_policies": [
                    {
                        "name": "custom",
                        "acl_mode": "static",
                        "classification": "restricted",
                    }
                ]
            }
        },
        environment="production",
        workspace="acme",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )
    repository = MemoryRepository()
    catalog = ConfiguredSourceCatalog(settings)
    workflow = EventIngestionWorkflow(
        repository.ingestion,
        KoreanNgramAnalyzer(),
        LocalContentAddressedStore(tmp_path / "cas"),
    )
    event = ConnectorEvent(
        event_id="evt_1",
        connector_name="custom",
        operation="upsert",
        external_id="record-1",
        payload={"text": "secret", "classification": "public"},
        acl_scopes=["workspace:acme"],
    )
    selected = event.model_copy(
        update={"acl_snapshot": catalog.event_acl_snapshot(event)}
    )

    result = workflow.ingest(
        RequestContext(workspace="acme", acl_scopes=["workspace:acme"]),
        selected,
        classification=catalog.event_classification(event),
    )

    unit = repository.state.units[
        next(iter(repository.state.units))
    ]
    assert result.status == "inserted"
    assert unit.classification is DataClassification.RESTRICTED
    source_object = next(iter(repository.state.artifacts.values())).source_object
    assert source_object is not None
    assert source_object.classification is DataClassification.RESTRICTED


def test_container_builds_one_central_remote_egress_policy(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "security": {"allow_remote_model_egress": True},
            "models": {
                "generation": {
                    "enabled": True,
                    "provider": "anthropic",
                    "allowed_classifications": ["public", "internal"],
                    "retention_policy": "zero_retention",
                    "secret_ref": "env:KIP_ANTHROPIC_API_KEY",
                }
            },
            "search": {"semantic_enabled": False},
            "graph": {"backend": "memory"},
        },
        environment="test",
        workspace="acme",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )

    policy = build_container(settings).application.egress.policy

    assert policy.provider is EgressProvider.ANTHROPIC
    assert policy.allowed_classifications == (
        DataClassification.PUBLIC,
        DataClassification.INTERNAL,
    )
    assert policy.retention_policy is RetentionPolicy.ZERO_RETENTION
    assert policy.secret_reference == "env:KIP_ANTHROPIC_API_KEY"
