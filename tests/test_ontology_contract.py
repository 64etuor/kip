from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.models import AssertionCandidate
from kip.errors import ValidationError
from kip.ids import new_id
from kip.ontology import OntologyCatalog, validate_ontology
from kip.ontology_migration import (
    OntologyMigration,
    diff_ontologies,
    validate_migration_coverage,
)
from kip.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def _copy_ontology(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(ROOT / "ontology", target)
    return target


def _yaml(path: Path) -> dict[str, object]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _predicates(root: Path) -> tuple[Path, dict[str, object]]:
    path = root / "core/predicates.yaml"
    payload = _yaml(path)
    definitions = payload["predicates"]
    assert isinstance(definitions, dict)
    return path, definitions


def test_repository_ontology_contract_is_consistent() -> None:
    assert validate_ontology(ROOT / "ontology") == []


def test_ontology_contract_detects_missing_predicate_risk(tmp_path: Path) -> None:
    copied = tmp_path / "ontology"
    shutil.copytree(ROOT / "ontology", copied)
    predicates = copied / "core/predicates.yaml"
    predicates.write_text(
        predicates.read_text(encoding="utf-8").replace("    risk: low\n", "", 1),
        encoding="utf-8",
    )

    assert "predicate authored_by: invalid risk level" in validate_ontology(copied)


def test_ontology_catalog_rejects_unknown_predicates_and_versions() -> None:
    catalog = OntologyCatalog.load(ROOT / "ontology")

    catalog.validate_candidate("amends", "core/1.0.0")
    with pytest.raises(ValidationError, match="unknown ontology predicate"):
        catalog.validate_candidate("invented_relation", "core/1.0.0")
    with pytest.raises(ValidationError, match="ontology version"):
        catalog.validate_candidate("amends", "core/9.9.9")


def test_application_rejects_candidate_outside_ontology_contract(tmp_path: Path) -> None:
    settings = Settings(
        project_root=ROOT,
        config_path=tmp_path / "kip.toml",
        raw={"parsers": {"hwp": {"order": ["paired_pdf"]}}},
        environment="test",
        workspace="default",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )
    container = build_container(settings, repository=MemoryRepository())
    context = container.application.operations.request_context()
    candidate = AssertionCandidate(
        id=new_id("cand"),
        subject_id="doc_new",
        predicate="invented_relation",
        object_entity_id="doc_old",
        origin="test",
        ontology_version="core/1.0.0",
    )

    with pytest.raises(ValidationError, match="unknown ontology predicate"):
        container.application.knowledge.create_candidate(context, candidate)


def test_ontology_diff_marks_addition_as_compatible(tmp_path: Path) -> None:
    # Given a release that only adds a low-risk predicate
    before = _copy_ontology(tmp_path, "before")
    after = _copy_ontology(tmp_path, "after")
    path, predicates = _predicates(after)
    predicates["mentions"] = {
        "domain": ["EvidenceObject"],
        "range": ["Concept"],
        "inverse": None,
        "risk": "low",
        "extraction": "semantic",
        "review": "not_required",
    }
    _write_yaml(path, {"ontology": "kip-core-predicates", "version": "1.1.0", "predicates": predicates})

    # When the releases are compared
    result = diff_ontologies(before, after)

    # Then the release can be adopted without rewriting assertions
    assert result.classification == "compatible"
    assert result.changes[0].change_type == "predicate_added"


@pytest.mark.parametrize(
    ("mutation", "expected_change"),
    [
        ("remove_predicate", "predicate_removed"),
        ("change_parent", "entity_parent_changed"),
        ("narrow_domain", "predicate_domain_narrowed"),
        ("weaken_risk", "predicate_risk_weakened"),
        ("weaken_review", "predicate_review_weakened"),
    ],
)
def test_ontology_diff_marks_meaning_loss_as_breaking(
    tmp_path: Path,
    mutation: str,
    expected_change: str,
) -> None:
    # Given a release that invalidates or weakens existing meaning
    before = _copy_ontology(tmp_path, f"before-{mutation}")
    after = _copy_ontology(tmp_path, f"after-{mutation}")
    predicate_path, predicates = _predicates(after)
    if mutation == "remove_predicate":
        del predicates["responsible_for"]
    elif mutation == "narrow_domain":
        definition = predicates["belongs_to_project"]
        assert isinstance(definition, dict)
        definition["domain"] = ["EvidenceObject", "Decision", "Requirement"]
    elif mutation == "weaken_risk":
        definition = predicates["responsible_for"]
        assert isinstance(definition, dict)
        definition["risk"] = "low"
    elif mutation == "weaken_review":
        definition = predicates["responsible_for"]
        assert isinstance(definition, dict)
        definition["review"] = "not_required"
    if mutation == "change_parent":
        entity_path = after / "core/entity-types.yaml"
        entities_payload = _yaml(entity_path)
        entities = entities_payload["entity_types"]
        assert isinstance(entities, dict)
        task = entities["Task"]
        assert isinstance(task, dict)
        task["parent"] = "Concept"
        _write_yaml(entity_path, entities_payload)
    _write_yaml(
        predicate_path,
        {"ontology": "kip-core-predicates", "version": "2.0.0", "predicates": predicates},
    )

    # When the releases are compared
    result = diff_ontologies(before, after)

    # Then a migration is mandatory
    assert result.classification == "breaking"
    assert expected_change in {change.change_type for change in result.changes}
    assert validate_migration_coverage(result, None) != []


def test_ontology_diff_marks_widening_and_policy_tightening_for_review(
    tmp_path: Path,
) -> None:
    # Given a release that broadens applicability and tightens review
    before = _copy_ontology(tmp_path, "before-review")
    after = _copy_ontology(tmp_path, "after-review")
    path, predicates = _predicates(after)
    definition = predicates["responsible_for"]
    assert isinstance(definition, dict)
    definition["domain"] = ["Person", "Organization", "Document"]
    definition["risk"] = "high"
    definition["review"] = "required"
    _write_yaml(path, {"ontology": "kip-core-predicates", "version": "1.1.0", "predicates": predicates})
    review_path = after / "policies/review-policy.yaml"
    review = _yaml(review_path)
    required = review["human_review_required"]
    assert isinstance(required, dict)
    names = required["predicates"]
    assert isinstance(names, list)
    names.append("responsible_for")
    _write_yaml(review_path, review)

    # When the releases are compared
    result = diff_ontologies(before, after)

    # Then an operator must review the expanded meaning
    assert result.classification == "review_required"


def test_migration_covers_removed_symbol_only_with_valid_target(tmp_path: Path) -> None:
    # Given a breaking predicate replacement
    before = _copy_ontology(tmp_path, "before-migration")
    after = _copy_ontology(tmp_path, "after-migration")
    path, predicates = _predicates(after)
    old = predicates.pop("responsible_for")
    predicates["owns_responsibility_for"] = old
    _write_yaml(path, {"ontology": "kip-core-predicates", "version": "2.0.0", "predicates": predicates})
    diff = diff_ontologies(before, after)
    migration = OntologyMigration.model_validate(
        {
            "schema_version": "kip.ontology-migration.v1",
            "from_version": "core/1.0.0",
            "to_version": "core/2.0.0",
            "operations": [
                {
                    "operation": "replace",
                    "symbol_kind": "predicate",
                    "sources": ["responsible_for"],
                    "targets": ["owns_responsibility_for"],
                    "review_required": True,
                }
            ],
        }
    )

    # When migration coverage is checked
    errors = validate_migration_coverage(diff, migration)

    # Then every breaking source is explicitly mapped
    assert errors == []

    # Given the same migration with a target absent from the new release
    invalid = migration.model_copy(
        update={
            "operations": (
                migration.operations[0].model_copy(update={"targets": ("missing",)}),
            )
        }
    )

    # When/Then target validity is checked
    assert "unknown target predicate missing" in validate_migration_coverage(diff, invalid)
