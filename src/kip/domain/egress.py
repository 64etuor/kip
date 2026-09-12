from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Iterable
from enum import StrEnum, unique
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


@unique
class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    PERSONAL = "personal"


@unique
class EgressProvider(StrEnum):
    LOCAL = "local"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@unique
class RetentionPolicy(StrEnum):
    PROVIDER_DEFAULT = "provider_default"
    ZERO_RETENTION = "zero_retention"


@unique
class EgressDenialReason(StrEnum):
    NO_EVIDENCE = "no_evidence"
    POLICY_DISABLED = "policy_disabled"
    MISSING_PROVIDER = "missing_provider"
    MISSING_CLASSIFICATION = "missing_classification"
    REMOTE_EGRESS_DISABLED = "remote_egress_disabled"
    MISSING_RETENTION_POLICY = "missing_retention_policy"
    RETENTION_NOT_ALLOWED = "retention_not_allowed"
    MISSING_SECRET_REFERENCE = "missing_secret_reference"
    INVALID_LOCAL_ENDPOINT = "invalid_local_endpoint"
    CLASSIFICATION_NOT_ALLOWED = "classification_not_allowed"


class EgressModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClassifiedEvidence(EgressModel):
    id: str = Field(min_length=1)
    classification: DataClassification | None


class EgressPolicy(EgressModel):
    enabled: bool = False
    provider: EgressProvider | None = None
    allow_remote: bool = False
    allowed_classifications: tuple[DataClassification, ...] = ()
    retention_policy: RetentionPolicy | None = None
    secret_reference: str | None = Field(default=None, repr=False)
    base_url: str | None = None
    model_service_hosts: tuple[str, ...] = ()

    @field_validator("model_service_hosts")
    @classmethod
    def bare_service_names(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_model_service_hosts(values)

    @field_validator("allowed_classifications")
    @classmethod
    def unique_classifications(
        cls,
        values: tuple[DataClassification, ...],
    ) -> tuple[DataClassification, ...]:
        if len(values) != len(set(values)):
            raise ValueError("allowed classifications must be unique")
        return values

    def fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"secret_reference"},
        )
        payload["has_secret_reference"] = bool(self.secret_reference)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


class EgressDecision(EgressModel):
    allowed: bool
    provider: EgressProvider | None
    allowed_evidence_ids: tuple[str, ...] = ()
    denied_evidence_ids: tuple[str, ...] = ()
    denial_reason: EgressDenialReason | None = None
    policy_fingerprint: str


def evaluate_egress(
    policy: EgressPolicy,
    evidence: list[ClassifiedEvidence],
) -> EgressDecision:
    fingerprint = policy.fingerprint()
    all_ids = tuple(item.id for item in evidence)
    if not evidence:
        return _deny(policy, fingerprint, (), EgressDenialReason.NO_EVIDENCE)
    if not policy.enabled:
        return _deny(
            policy,
            fingerprint,
            all_ids,
            EgressDenialReason.POLICY_DISABLED,
        )
    if policy.provider is None:
        return _deny(
            policy,
            fingerprint,
            all_ids,
            EgressDenialReason.MISSING_PROVIDER,
        )
    if any(item.classification is None for item in evidence):
        return _deny(
            policy,
            fingerprint,
            all_ids,
            EgressDenialReason.MISSING_CLASSIFICATION,
        )

    if policy.provider is EgressProvider.LOCAL:
        if not is_local_model_endpoint(policy.base_url, policy.model_service_hosts):
            return _deny(
                policy,
                fingerprint,
                all_ids,
                EgressDenialReason.INVALID_LOCAL_ENDPOINT,
            )
        return EgressDecision(
            allowed=True,
            provider=policy.provider,
            allowed_evidence_ids=all_ids,
            policy_fingerprint=fingerprint,
        )

    if not policy.allow_remote:
        return _deny(
            policy,
            fingerprint,
            all_ids,
            EgressDenialReason.REMOTE_EGRESS_DISABLED,
        )
    if not policy.secret_reference or not _is_secret_reference(
        policy.secret_reference
    ):
        return _deny(
            policy,
            fingerprint,
            all_ids,
            EgressDenialReason.MISSING_SECRET_REFERENCE,
        )
    if policy.retention_policy is None:
        return _deny(
            policy,
            fingerprint,
            all_ids,
            EgressDenialReason.MISSING_RETENTION_POLICY,
        )

    classifications = {
        item.classification
        for item in evidence
        if item.classification is not None
    }
    if (
        classifications - {DataClassification.PUBLIC}
        and policy.retention_policy is not RetentionPolicy.ZERO_RETENTION
    ):
        return _deny(
            policy,
            fingerprint,
            all_ids,
            EgressDenialReason.RETENTION_NOT_ALLOWED,
        )

    allowed_set = set(policy.allowed_classifications)
    allowed_ids = tuple(
        item.id for item in evidence if item.classification in allowed_set
    )
    denied_ids = tuple(
        item.id for item in evidence if item.classification not in allowed_set
    )
    if denied_ids:
        return EgressDecision(
            allowed=False,
            provider=policy.provider,
            allowed_evidence_ids=allowed_ids,
            denied_evidence_ids=denied_ids,
            denial_reason=EgressDenialReason.CLASSIFICATION_NOT_ALLOWED,
            policy_fingerprint=fingerprint,
        )
    return EgressDecision(
        allowed=True,
        provider=policy.provider,
        allowed_evidence_ids=allowed_ids,
        policy_fingerprint=fingerprint,
    )


def _deny(
    policy: EgressPolicy,
    fingerprint: str,
    evidence_ids: tuple[str, ...],
    reason: EgressDenialReason,
) -> EgressDecision:
    return EgressDecision(
        allowed=False,
        provider=policy.provider,
        denied_evidence_ids=evidence_ids,
        denial_reason=reason,
        policy_fingerprint=fingerprint,
    )


# A deployment's model service is named, never guessed. Bare service names
# only (compose `models`): a dotted name or an IP would quietly re-open the
# remote egress `security.allow_remote_model_egress = false` keeps closed.
_BARE_SERVICE_NAME = re.compile(r"[a-z][a-z0-9_-]*")


def normalize_model_service_hosts(hosts: Iterable[str]) -> tuple[str, ...]:
    """Validate `security.model_service_hosts` into comparable host names.

    Raises ``ValueError`` on anything that is not a bare service name, so an
    allowlist entry can only ever widen the local predicate by one named
    in-deployment service.
    """
    normalized: list[str] = []
    for name in hosts:
        candidate = str(name).strip().lower()
        if not _BARE_SERVICE_NAME.fullmatch(candidate):
            raise ValueError(
                "security.model_service_hosts entries must be bare service names, "
                f"not {name!r}"
            )
        if candidate not in normalized:
            normalized.append(candidate)
    return tuple(normalized)


def is_local_model_endpoint(
    base_url: str | None,
    model_service_hosts: Iterable[str] = (),
) -> bool:
    """The single local-endpoint predicate shared by every model call site.

    Strict by default: only a loopback address (every 127.0.0.0/8 address,
    ``::1`` and the literal ``localhost``) counts as local. It widens only for
    a host explicitly named in ``security.model_service_hosts``, which is how a
    container deployment reaches its own ``models`` service without enabling
    remote egress. The egress policy and the HTTP model adapters must agree
    here: a host one of them treats as local and the other does not is a
    deployment that embeds where it may not generate.
    """
    if not base_url:
        return False
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower()
    try:
        if ipaddress.ip_address(host).is_loopback:
            return True
    except ValueError:
        if host == "localhost":
            return True
    return host in normalize_model_service_hosts(model_service_hosts)


def _is_secret_reference(value: str) -> bool:
    scheme, separator, name = value.partition(":")
    return bool(
        separator
        and name.strip()
        and scheme in {"env", "file"}
    )
