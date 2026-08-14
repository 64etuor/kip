from __future__ import annotations

import shutil
import stat
from pathlib import Path

import pytest
import yaml

from kip.domain.interactions import (
    DiscoveryKind,
    OntologyDiscoveryCandidate,
    ReviewPolicy,
    RiskLevel,
)
from kip.errors import ValidationError
from kip.ontology import OntologyCatalog
from kip.ontology_discovery_release import materialize_ontology_release

ROOT = Path(__file__).resolve().parents[1]


def _ontology_root(tmp_path: Path) -> Path:
    target = tmp_path / "ontology"
    shutil.copytree(ROOT / "ontology", target)
    return target


def _candidate(
    *,
    kind: DiscoveryKind,
    symbol: str,
    domain_profile: str = "research-project",
    label: str = "테스트 라벨",
    definition: str = "테스트 정의를 표현한다.",
    target_symbol: str | None = None,
    parent: str | None = None,
    domain: list[str] | None = None,
    range: list[str] | None = None,
    inverse: str | None = None,
    risk: RiskLevel | None = None,
    review: ReviewPolicy | None = None,
    extraction: str | None = None,
) -> OntologyDiscoveryCandidate:
    return OntologyDiscoveryCandidate(
        domain_profile=domain_profile,
        kind=kind,
        symbol=symbol,
        label=label,
        definition=definition,
        target_symbol=target_symbol,
        parent=parent,
        domain=domain,
        range=range,
        inverse=inverse,
        risk=risk,
        review=review,
        extraction=extraction,
        fingerprint="sha256:" + "0" * 64,
    )


def test_new_entity_type_into_an_empty_profile_becomes_a_root(tmp_path: Path) -> None:
    ontology_root = _ontology_root(tmp_path)
    candidate = _candidate(
        kind="entity_type",
        symbol="contract",
        domain_profile="empty",
        label="계약",
        definition="업무상 체결하는 계약을 표현한다.",
    )

    release = materialize_ontology_release(ontology_root, "empty", candidate)

    assert release.kind == "entity_type"
    assert release.symbol == "contract"
    assert release.file == "domains/empty.yaml"
    assert release.version == "1.1.0"
    assert release.catalog_refresh == "restart_required"

    domain_path = ontology_root / "domains" / "empty.yaml"
    payload = yaml.safe_load(domain_path.read_text(encoding="utf-8"))
    assert payload["version"] == "1.1.0"
    assert payload["entity_types"]["contract"] == {
        "label_ko": "계약",
        "description_ko": "업무상 체결하는 계약을 표현한다.",
    }
    # Loading the released tree must reflect the new symbol immediately.
    catalog = OntologyCatalog.load(ontology_root, domain_profile="empty")
    catalog.validate_entity_type("contract")
    assert catalog.entity_parents["contract"] is None


def test_new_entity_type_with_an_explicit_parent_preserves_comments(tmp_path: Path) -> None:
    ontology_root = _ontology_root(tmp_path)
    domain_path = ontology_root / "domains" / "research-project.yaml"
    comment_line = "# migration manifest). Structural changes still require a new release."
    before = domain_path.read_text(encoding="utf-8")
    assert comment_line in before

    candidate = _candidate(
        kind="entity_type",
        symbol="framework_agreement",
        domain_profile="research-project",
        label="기본협약",
        definition="개별 협약의 기준이 되는 기본 협약을 표현한다.",
        parent="Document",
    )

    release = materialize_ontology_release(ontology_root, "research-project", candidate)

    assert release.version == "1.1.0"
    after = domain_path.read_text(encoding="utf-8")
    assert comment_line in after
    payload = yaml.safe_load(after)
    assert payload["entity_types"]["framework_agreement"]["parent"] == "Document"
    # The new entry must land inside `entity_types`, before the sibling
    # `controlled_values` section, not appended blindly at EOF.
    assert after.index("framework_agreement:") < after.index("controlled_values:")
    catalog = OntologyCatalog.load(ontology_root, domain_profile="research-project")
    assert catalog.is_a("framework_agreement", "Document")


def test_new_predicate_defaults_and_syncs_review_policy(tmp_path: Path) -> None:
    ontology_root = _ontology_root(tmp_path)
    predicates_path = ontology_root / "core" / "predicates.yaml"
    comment_line = "# non-breaking metadata change that does not require a new release version or"
    assert comment_line in predicates_path.read_text(encoding="utf-8")

    candidate = _candidate(
        kind="predicate",
        symbol="cites",
        label="인용",
        definition="한 문서가 다른 문서를 인용한다.",
    )

    release = materialize_ontology_release(ontology_root, "research-project", candidate)

    assert release.file == "core/predicates.yaml"
    assert release.version == "1.1.0"
    predicates_text = predicates_path.read_text(encoding="utf-8")
    assert comment_line in predicates_text
    predicates_payload = yaml.safe_load(predicates_text)
    assert predicates_payload["version"] == "1.1.0"
    cites = predicates_payload["predicates"]["cites"]
    assert cites["domain"] == ["EvidenceObject"]
    assert cites["range"] == ["EvidenceObject"]
    assert cites["inverse"] is None
    assert cites["risk"] == "high"
    assert cites["review"] == "required"
    assert cites["extraction"] == "semantic"

    review_policy_path = ontology_root / "policies" / "review-policy.yaml"
    review_policy = yaml.safe_load(review_policy_path.read_text(encoding="utf-8"))
    assert "cites" in review_policy["human_review_required"]["predicates"]

    catalog = OntologyCatalog.load(ontology_root, domain_profile="research-project")
    assert "cites" in catalog.evidence_required_predicates()


def test_predicate_with_explicit_low_risk_spec_skips_review_policy_sync(
    tmp_path: Path,
) -> None:
    ontology_root = _ontology_root(tmp_path)
    review_policy_path = ontology_root / "policies" / "review-policy.yaml"
    before = review_policy_path.read_text(encoding="utf-8")

    candidate = _candidate(
        kind="predicate",
        symbol="mentions",
        label="언급",
        definition="한 문서가 다른 개체를 언급한다.",
        domain=["Communication"],
        range=["Person"],
        inverse=None,
        risk="low",
        review="not_required",
        extraction="deterministic_source_relation",
    )

    materialize_ontology_release(ontology_root, "research-project", candidate)

    assert review_policy_path.read_text(encoding="utf-8") == before
    predicates_payload = yaml.safe_load(
        (ontology_root / "core" / "predicates.yaml").read_text(encoding="utf-8")
    )
    mentions = predicates_payload["predicates"]["mentions"]
    assert mentions["domain"] == ["Communication"]
    assert mentions["range"] == ["Person"]
    assert mentions["risk"] == "low"
    assert mentions["review"] == "not_required"


def test_entity_type_materialization_is_idempotent_on_retry(tmp_path: Path) -> None:
    ontology_root = _ontology_root(tmp_path)
    candidate = _candidate(kind="entity_type", symbol="side_letter", label="부속서", definition="d")

    first = materialize_ontology_release(ontology_root, "research-project", candidate)
    domain_path = ontology_root / "domains" / "research-project.yaml"
    after_first = domain_path.read_text(encoding="utf-8")

    second = materialize_ontology_release(ontology_root, "research-project", candidate)

    assert second == first
    assert domain_path.read_text(encoding="utf-8") == after_first


def test_predicate_materialization_is_idempotent_on_retry(tmp_path: Path) -> None:
    ontology_root = _ontology_root(tmp_path)
    candidate = _candidate(kind="predicate", symbol="clarifies", label="명확화", definition="d")

    first = materialize_ontology_release(ontology_root, "research-project", candidate)
    predicates_path = ontology_root / "core" / "predicates.yaml"
    after_first = predicates_path.read_text(encoding="utf-8")

    second = materialize_ontology_release(ontology_root, "research-project", candidate)

    assert second == first
    assert predicates_path.read_text(encoding="utf-8") == after_first


def test_materialization_fails_closed_on_a_read_only_ontology_root(tmp_path: Path) -> None:
    ontology_root = _ontology_root(tmp_path)
    candidate = _candidate(kind="entity_type", symbol="side_letter", label="부속서", definition="d")
    mode = ontology_root.stat().st_mode
    ontology_root.chmod(mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
    try:
        with pytest.raises(ValidationError, match="not writable"):
            materialize_ontology_release(ontology_root, "research-project", candidate)
    finally:
        ontology_root.chmod(mode)


def test_invalid_explicit_parent_fails_shadow_validation_without_touching_files(
    tmp_path: Path,
) -> None:
    ontology_root = _ontology_root(tmp_path)
    domain_path = ontology_root / "domains" / "research-project.yaml"
    before = domain_path.read_text(encoding="utf-8")
    candidate = _candidate(
        kind="entity_type",
        symbol="side_letter",
        label="부속서",
        definition="d",
        parent="NoSuchEntityType",
    )

    with pytest.raises(ValidationError, match="shadow validation"):
        materialize_ontology_release(ontology_root, "research-project", candidate)

    assert domain_path.read_text(encoding="utf-8") == before


def test_invalid_predicate_domain_fails_shadow_validation_without_touching_files(
    tmp_path: Path,
) -> None:
    ontology_root = _ontology_root(tmp_path)
    predicates_path = ontology_root / "core" / "predicates.yaml"
    review_policy_path = ontology_root / "policies" / "review-policy.yaml"
    predicates_before = predicates_path.read_text(encoding="utf-8")
    review_policy_before = review_policy_path.read_text(encoding="utf-8")
    candidate = _candidate(
        kind="predicate",
        symbol="cites",
        label="인용",
        definition="d",
        domain=["NoSuchEntityType"],
    )

    with pytest.raises(ValidationError, match="shadow validation"):
        materialize_ontology_release(ontology_root, "research-project", candidate)

    assert predicates_path.read_text(encoding="utf-8") == predicates_before
    assert review_policy_path.read_text(encoding="utf-8") == review_policy_before


def test_unreleasable_kind_raises_without_touching_files(tmp_path: Path) -> None:
    ontology_root = _ontology_root(tmp_path)
    candidate = _candidate(
        kind="alias",
        symbol="agreement_alias",
        target_symbol="Agreement",
        label="별칭",
        definition="d",
    )

    with pytest.raises(ValidationError, match="does not release automatically"):
        materialize_ontology_release(ontology_root, "research-project", candidate)
