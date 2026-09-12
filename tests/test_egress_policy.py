from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from kip.adapters.analyzers.korean_ngram import KoreanNgramAnalyzer
from kip.adapters.connectors.registry import ConfiguredSourceCatalog
from kip.adapters.embeddings.http import require_allowed_model_url
from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.storage import LocalContentAddressedStore
from kip.application.egress import EgressPolicyUseCases
from kip.application.ingestion_events import EventFamily, EventIngestionWorkflow
from kip.container import build_container
from kip.domain.egress import (
    ClassifiedEvidence,
    DataClassification,
    EgressDenialReason,
    EgressPolicy,
    EgressProvider,
    RetentionPolicy,
    evaluate_egress,
    is_local_model_endpoint,
)
from kip.domain.models import ConnectorEvent, ContentUnit, EvidenceLocator, RequestContext
from kip.errors import ConfigurationError
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
        family=EventFamily(catalog.event_family(event.connector_name)),
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

    policy = build_container(settings, load_models=False).application.egress.policy

    assert policy.provider is EgressProvider.ANTHROPIC
    assert policy.allowed_classifications == (
        DataClassification.PUBLIC,
        DataClassification.INTERNAL,
    )
    assert policy.retention_policy is RetentionPolicy.ZERO_RETENTION
    assert policy.secret_reference == "env:KIP_ANTHROPIC_API_KEY"


LOCAL_HOST_URLS = (
    "http://127.0.0.1:7998",
    "http://127.9.9.9:7998",
    "http://[::1]:7998",
    "http://localhost:7998",
    "https://localhost:7998",
)
NON_LOCAL_HOST_URLS = (
    "http://models:7997",
    "http://10.0.0.5:7998",
    "http://models.example.com:7998",
    "https://api.openai.com",
)


def _local_policy(
    base_url: str | None,
    *,
    model_service_hosts: tuple[str, ...] = (),
) -> EgressPolicy:
    return EgressPolicy(
        enabled=True,
        provider=EgressProvider.LOCAL,
        allow_remote=False,
        allowed_classifications=CLASSIFICATIONS,
        base_url=base_url,
        model_service_hosts=model_service_hosts,
    )


@pytest.mark.parametrize("base_url", LOCAL_HOST_URLS)
def test_local_policy_admits_every_loopback_form(base_url: str) -> None:
    decision = evaluate_egress(
        _local_policy(base_url),
        [_evidence(DataClassification.RESTRICTED)],
    )

    assert decision.allowed is True


@pytest.mark.parametrize("base_url", NON_LOCAL_HOST_URLS)
def test_local_policy_denies_unnamed_hosts_with_a_typed_reason(base_url: str) -> None:
    decision = evaluate_egress(
        _local_policy(base_url),
        [_evidence(DataClassification.PUBLIC)],
    )

    assert decision.allowed is False
    assert decision.denial_reason is EgressDenialReason.INVALID_LOCAL_ENDPOINT
    assert decision.denied_evidence_ids == ("unit_1",)


def test_local_policy_widens_only_for_an_explicitly_allowlisted_service() -> None:
    allowed = evaluate_egress(
        _local_policy("http://models:7997", model_service_hosts=("models",)),
        [_evidence(DataClassification.CONFIDENTIAL)],
    )
    other_service = evaluate_egress(
        _local_policy("http://models:7997", model_service_hosts=("embeddings",)),
        [_evidence(DataClassification.CONFIDENTIAL)],
    )

    assert allowed.allowed is True
    assert other_service.allowed is False
    assert other_service.denial_reason is EgressDenialReason.INVALID_LOCAL_ENDPOINT


@pytest.mark.parametrize(
    "base_url",
    [
        "http://user:secret@127.0.0.1:7998",
        "ftp://127.0.0.1:7998",
        "127.0.0.1:7998",
        "",
        None,
    ],
)
def test_local_policy_denies_endpoints_that_are_not_plain_local_http(
    base_url: str | None,
) -> None:
    decision = evaluate_egress(
        _local_policy(base_url),
        [_evidence(DataClassification.PUBLIC)],
    )

    assert decision.allowed is False
    assert decision.denial_reason is EgressDenialReason.INVALID_LOCAL_ENDPOINT


@pytest.mark.parametrize(
    "entry", ["models.example.com", "10.0.0.5", "", "*", "models:7997", "-models"]
)
def test_policy_rejects_model_service_hosts_that_are_not_bare_service_names(
    entry: str,
) -> None:
    with pytest.raises(PydanticValidationError, match="bare service names"):
        _local_policy("http://127.0.0.1:7998", model_service_hosts=(entry,))


def test_allowlisted_service_names_are_compared_case_insensitively() -> None:
    policy = _local_policy("http://MODELS:7997", model_service_hosts=(" Models ",))

    decision = evaluate_egress(policy, [_evidence(DataClassification.PUBLIC)])

    assert policy.model_service_hosts == ("models",)
    assert decision.allowed is True


def test_policy_fingerprint_covers_the_local_host_allowlist() -> None:
    strict = _local_policy("http://models:7997")
    widened = _local_policy("http://models:7997", model_service_hosts=("models",))

    assert strict.fingerprint() != widened.fingerprint()


@pytest.mark.parametrize(
    "base_url", [*LOCAL_HOST_URLS, *NON_LOCAL_HOST_URLS, "http://models:7997"]
)
@pytest.mark.parametrize("model_service_hosts", [(), ("models",)])
def test_model_adapters_and_egress_policy_share_one_local_predicate(
    base_url: str,
    model_service_hosts: tuple[str, ...],
) -> None:
    # Given the domain predicate's verdict for this endpoint
    expected = is_local_model_endpoint(base_url, model_service_hosts)

    # When the model adapters admit or refuse the same endpoint
    try:
        require_allowed_model_url(base_url, False, model_service_hosts)
        adapter_allows = True
    except ConfigurationError:
        adapter_allows = False

    # And when a local generation policy decides on the same endpoint
    decision = evaluate_egress(
        _local_policy(base_url, model_service_hosts=model_service_hosts),
        [_evidence(DataClassification.PUBLIC)],
    )

    # Then embedding, reranking and generation never disagree about locality
    assert adapter_allows is expected
    assert decision.allowed is expected


def _container_model_settings(
    tmp_path: Path,
    *,
    model_service_hosts: list[str],
) -> Settings:
    return Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "security": {
                "allow_remote_model_egress": False,
                "model_service_hosts": model_service_hosts,
            },
            "models": {
                "embedding": {
                    "enabled": True,
                    "base_url": "http://models:7997",
                    "model": "kip-embedding",
                    "revision": "abc123",
                },
                "generation": {
                    "enabled": True,
                    "provider": "local",
                    "base_url": "http://models:7998",
                    "model": "local-answer-model",
                    "revision": "abc123",
                },
            },
            "search": {"semantic_enabled": False},
            "graph": {"backend": "memory"},
        },
        environment="test",
        workspace="acme",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )


def test_container_carries_the_allowlist_into_the_generation_egress_policy(
    tmp_path: Path,
) -> None:
    # Given a container deployment that names its own model service
    settings = _container_model_settings(tmp_path, model_service_hosts=["models"])

    # When the container builds its central egress policy
    policy = build_container(settings, load_models=False).application.egress.policy

    # Then the same service the embedding adapter reaches may also generate
    assert policy.model_service_hosts == ("models",)
    decision = evaluate_egress(policy, [_evidence(DataClassification.CONFIDENTIAL)])
    assert decision.allowed is True


def test_container_without_an_allowlist_refuses_the_same_service_host(
    tmp_path: Path,
) -> None:
    # Given the same deployment with no named model service
    settings = _container_model_settings(tmp_path, model_service_hosts=[])

    # When the container builds its central egress policy
    policy = build_container(settings, load_models=False).application.egress.policy

    # Then generation is refused with the typed reason, not silently allowed
    decision = evaluate_egress(policy, [_evidence(DataClassification.PUBLIC)])
    assert policy.model_service_hosts == ()
    assert decision.allowed is False
    assert decision.denial_reason is EgressDenialReason.INVALID_LOCAL_ENDPOINT


def test_container_rejects_a_model_service_host_that_is_not_a_bare_name(
    tmp_path: Path,
) -> None:
    settings = _container_model_settings(
        tmp_path, model_service_hosts=["models.example.com"]
    )

    with pytest.raises(ConfigurationError, match="bare service names"):
        build_container(settings, load_models=False)
