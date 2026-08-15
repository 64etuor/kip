from __future__ import annotations

import json
import shutil
import stat
import threading
import time
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError as PydanticValidationError

from kip.domain.interactions import (
    DiscoveryKind,
    OntologyDiscoveryCandidate,
    ReviewPolicy,
    RiskLevel,
)
from kip.errors import ConflictError, ValidationError
from kip.ontology import OntologyCatalog
from kip.ontology_discovery_release import (
    RELEASE_JOURNAL_FILENAME,
    complete_pending_release,
    has_pending_release,
    materialize_ontology_release,
)

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


def test_candidate_construction_rejects_a_yaml_hostile_symbol() -> None:
    # `_materialize_predicate`/`_materialize_entity_type` interpolate
    # `candidate.symbol` as a raw YAML mapping key. A candidate reconstructed
    # from a store adapter row (or built directly) with a symbol that would
    # break out of the intended YAML key position must be rejected at model
    # construction, not deep inside the materializer.
    with pytest.raises(PydanticValidationError, match="ontology discovery symbol is invalid"):
        _candidate(kind="predicate", symbol="cites:\n  evil: true")


def test_candidate_construction_rejects_an_invalid_parent_reference() -> None:
    with pytest.raises(
        PydanticValidationError, match="ontology discovery parent is invalid"
    ):
        _candidate(
            kind="entity_type",
            symbol="contract",
            parent="Document\nrogue_key: true",
        )


def test_candidate_construction_rejects_an_invalid_domain_reference() -> None:
    with pytest.raises(
        PydanticValidationError,
        match="ontology discovery entity type reference is invalid",
    ):
        _candidate(kind="predicate", symbol="cites", domain=["EvidenceObject]\ninjected: true"])


def test_candidate_construction_rejects_an_invalid_inverse_symbol() -> None:
    with pytest.raises(PydanticValidationError, match="ontology discovery symbol is invalid"):
        _candidate(kind="predicate", symbol="cites", inverse="cited by")


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
        # `target_symbol` for an `alias`/`controlled_value` candidate names an
        # existing lowercase symbol (mirrors `OntologyDiscoveryProposal`,
        # which already rejects a PascalCase entity-type reference here).
        target_symbol="amends",
        label="별칭",
        definition="d",
    )

    with pytest.raises(ValidationError, match="does not release automatically"):
        materialize_ontology_release(ontology_root, "research-project", candidate)


def test_predicate_domain_thread_through_validates_against_the_real_domain_profile(
    tmp_path: Path,
) -> None:
    # `ResearchProject` is only declared in the `research-project` domain
    # profile, not in `core/entity-types.yaml`. Before threading the real
    # `domain_profile` through, `_materialize_predicate` shadow-validated
    # against whatever domain profile sorts first alphabetically on disk
    # (`empty.yaml`, which declares no entity types), so a predicate citing
    # a domain-profile-only type could never be approved even when the
    # caller's real profile is `research-project`.
    ontology_root = _ontology_root(tmp_path)
    candidate = _candidate(
        kind="predicate",
        symbol="funds",
        label="지원한다",
        definition="한 조직이 연구과제를 지원한다.",
        domain=["Organization"],
        range=["ResearchProject"],
    )

    release = materialize_ontology_release(ontology_root, "research-project", candidate)

    assert release.symbol == "funds"
    catalog = OntologyCatalog.load(ontology_root, domain_profile="research-project")
    assert catalog.predicate_specs["funds"].range == ("ResearchProject",)


def test_predicate_materialization_against_the_empty_profile_still_validates(
    tmp_path: Path,
) -> None:
    # Deployments using the empty domain profile must keep working exactly
    # as before: a predicate whose domain/range only cite core entity types
    # still validates when `domain_profile="empty"` is the real profile.
    ontology_root = _ontology_root(tmp_path)
    candidate = _candidate(
        kind="predicate",
        symbol="cites",
        domain_profile="empty",
        label="인용",
        definition="한 문서가 다른 문서를 인용한다.",
    )

    release = materialize_ontology_release(ontology_root, "empty", candidate)

    assert release.symbol == "cites"
    catalog = OntologyCatalog.load(ontology_root, domain_profile="empty")
    assert "cites" in catalog.evidence_required_predicates()


def test_approving_a_different_entity_type_candidate_with_the_same_symbol_conflicts(
    tmp_path: Path,
) -> None:
    ontology_root = _ontology_root(tmp_path)
    first = _candidate(
        kind="entity_type",
        symbol="side_letter",
        label="부속서",
        definition="원 계약을 보완하는 부속 문서.",
    )
    materialize_ontology_release(ontology_root, "research-project", first)
    domain_path = ontology_root / "domains" / "research-project.yaml"
    after_first = domain_path.read_text(encoding="utf-8")

    divergent = _candidate(
        kind="entity_type",
        symbol="side_letter",
        label="다른 라벨",
        definition="완전히 다른 정의.",
    )

    with pytest.raises(ConflictError, match="already released with different content"):
        materialize_ontology_release(ontology_root, "research-project", divergent)

    # The conflicting approval must not have written anything.
    assert domain_path.read_text(encoding="utf-8") == after_first


def test_approving_a_different_predicate_candidate_with_the_same_symbol_conflicts(
    tmp_path: Path,
) -> None:
    ontology_root = _ontology_root(tmp_path)
    first = _candidate(
        kind="predicate",
        symbol="clarifies",
        label="명확화",
        definition="한 문서가 다른 문서를 명확히 한다.",
    )
    materialize_ontology_release(ontology_root, "research-project", first)
    predicates_path = ontology_root / "core" / "predicates.yaml"
    after_first = predicates_path.read_text(encoding="utf-8")

    divergent = _candidate(
        kind="predicate",
        symbol="clarifies",
        label="명확화",
        definition="한 문서가 다른 문서를 명확히 한다.",
        domain=["Communication"],
        risk="low",
        review="not_required",
    )

    with pytest.raises(ConflictError, match="already released with different content"):
        materialize_ontology_release(ontology_root, "research-project", divergent)

    assert predicates_path.read_text(encoding="utf-8") == after_first


def test_set_version_line_tolerates_and_preserves_a_trailing_comment(
    tmp_path: Path,
) -> None:
    ontology_root = _ontology_root(tmp_path)
    domain_path = ontology_root / "domains" / "research-project.yaml"
    commented = domain_path.read_text(encoding="utf-8").replace(
        "version: 1.0.0", "version: 1.0.0  # pinned by release process"
    )
    domain_path.write_text(commented, encoding="utf-8")
    candidate = _candidate(
        kind="entity_type",
        symbol="side_letter",
        label="부속서",
        definition="d",
    )

    release = materialize_ontology_release(ontology_root, "research-project", candidate)

    assert release.version == "1.1.0"
    after = domain_path.read_text(encoding="utf-8")
    assert "version: 1.1.0  # pinned by release process" in after


def test_complete_pending_release_heals_a_crashed_two_file_release(
    tmp_path: Path,
) -> None:
    ontology_root = _ontology_root(tmp_path)
    predicates_path = ontology_root / "core" / "predicates.yaml"
    review_policy_path = ontology_root / "policies" / "review-policy.yaml"
    healed_predicates_text = predicates_path.read_text(encoding="utf-8") + "\n# healed marker\n"
    healed_review_text = review_policy_path.read_text(encoding="utf-8") + "\n# healed marker\n"
    journal_path = ontology_root / RELEASE_JOURNAL_FILENAME
    journal_path.write_text(
        json.dumps(
            {
                "release": {"kind": "predicate", "symbol": "cites", "version": "1.1.0"},
                "files": {
                    "core/predicates.yaml": healed_predicates_text,
                    "policies/review-policy.yaml": healed_review_text,
                },
            }
        ),
        encoding="utf-8",
    )
    assert has_pending_release(ontology_root) is True

    healed = complete_pending_release(ontology_root)

    assert healed is True
    assert predicates_path.read_text(encoding="utf-8") == healed_predicates_text
    assert review_policy_path.read_text(encoding="utf-8") == healed_review_text
    assert not journal_path.is_file()
    assert has_pending_release(ontology_root) is False
    # Idempotent: a second call with no journal present is a clean no-op.
    assert complete_pending_release(ontology_root) is False


def test_complete_pending_release_rejects_a_malformed_journal(tmp_path: Path) -> None:
    ontology_root = _ontology_root(tmp_path)
    journal_path = ontology_root / RELEASE_JOURNAL_FILENAME
    journal_path.write_text("not json", encoding="utf-8")

    with pytest.raises(ValidationError, match="corrupt"):
        complete_pending_release(ontology_root)


def test_complete_pending_release_quarantines_a_corrupt_json_journal(
    tmp_path: Path,
) -> None:
    ontology_root = _ontology_root(tmp_path)
    predicates_path = ontology_root / "core" / "predicates.yaml"
    predicates_before = predicates_path.read_text(encoding="utf-8")
    journal_path = ontology_root / RELEASE_JOURNAL_FILENAME
    journal_path.write_text("not json", encoding="utf-8")

    with pytest.raises(ValidationError, match="corrupt"):
        complete_pending_release(ontology_root)

    # The corrupt journal must be renamed aside so it never bricks a future
    # start-up again, and the real tree must be left untouched.
    assert not journal_path.is_file()
    quarantined = ontology_root / (RELEASE_JOURNAL_FILENAME + ".rejected")
    assert quarantined.read_text(encoding="utf-8") == "not json"
    assert has_pending_release(ontology_root) is False
    assert predicates_path.read_text(encoding="utf-8") == predicates_before
    # A subsequent call is a clean no-op: no journal left to crash on again.
    assert complete_pending_release(ontology_root) is False


def test_complete_pending_release_quarantines_valid_json_that_fails_validation(
    tmp_path: Path,
) -> None:
    ontology_root = _ontology_root(tmp_path)
    predicates_path = ontology_root / "core" / "predicates.yaml"
    predicates_before = predicates_path.read_text(encoding="utf-8")
    # Valid JSON, but the journaled predicates.yaml content fails
    # `validate_ontology` (a `null` predicate definition, as if the journal
    # was hand-edited or torn between the journal write and the apply).
    broken_predicates_text = predicates_before.replace(
        "predicates:\n", "predicates:\n  broken_predicate:\n", 1
    )
    journal_path = ontology_root / RELEASE_JOURNAL_FILENAME
    journal_path.write_text(
        json.dumps(
            {
                "release": {
                    "kind": "predicate",
                    "symbol": "broken_predicate",
                    "version": "1.1.0",
                    "domain_profile": "research-project",
                },
                "files": {"core/predicates.yaml": broken_predicates_text},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="invalid ontology tree"):
        complete_pending_release(ontology_root)

    assert not journal_path.is_file()
    quarantined = ontology_root / (RELEASE_JOURNAL_FILENAME + ".rejected")
    assert quarantined.is_file()
    # The real tree must not be corrupted by a journal that fails validation.
    assert predicates_path.read_text(encoding="utf-8") == predicates_before
    assert has_pending_release(ontology_root) is False


def test_concurrent_materialization_is_serialized_by_the_release_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two near-simultaneous approvals for *different* symbols targeting the
    # same domain profile file used to race on read-modify-write: the last
    # `os.replace` would win and silently drop the other release, with the
    # version bumped only once. Widen the race window artificially (a small
    # sleep right before the real file write) so the test would reliably
    # fail without the release lock, then assert both symbols land and the
    # version is bumped exactly twice.
    ontology_root = _ontology_root(tmp_path)
    from kip import ontology_discovery_release

    original_atomic_write = ontology_discovery_release._atomic_write

    def slow_atomic_write(path: Path, text: str) -> None:
        time.sleep(0.05)
        original_atomic_write(path, text)

    monkeypatch.setattr(ontology_discovery_release, "_atomic_write", slow_atomic_write)

    candidate_a = _candidate(
        kind="entity_type", symbol="contract_a", label="계약 A", definition="첫 번째 계약."
    )
    candidate_b = _candidate(
        kind="entity_type", symbol="contract_b", label="계약 B", definition="두 번째 계약."
    )
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def run(name: str, candidate: OntologyDiscoveryCandidate) -> None:
        try:
            results[name] = materialize_ontology_release(
                ontology_root, "research-project", candidate
            )
        except BaseException as exc:
            errors.append(exc)

    thread_a = threading.Thread(target=run, args=("a", candidate_a))
    thread_b = threading.Thread(target=run, args=("b", candidate_b))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)

    assert not errors, errors
    domain_path = ontology_root / "domains" / "research-project.yaml"
    payload = yaml.safe_load(domain_path.read_text(encoding="utf-8"))
    assert "contract_a" in payload["entity_types"]
    assert "contract_b" in payload["entity_types"]
    # Both releases landed and each bumped the minor version once.
    assert payload["version"] == "1.2.0"
    assert not (ontology_root / RELEASE_JOURNAL_FILENAME).is_file()
