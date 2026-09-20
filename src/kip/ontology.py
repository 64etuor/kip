"""Read the YAML ontology tree under `ontology/` into domain data.

The tree is the on-disk form of the vocabulary; :mod:`kip.domain.ontology`
holds what it means. Use cases reach this reader through
:class:`kip.ports.ontology.OntologyCatalogPort`, never directly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from kip.domain.ontology import OntologyCatalog, PredicateSpec
from kip.errors import ValidationError

_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_PROFILE_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_EXTRACTION_POLICIES = frozenset(
    {
        "deterministic_when_source_identified",
        "deterministic_source_relation",
        "mixed",
        "semantic",
    }
)
_REVIEW_POLICIES = frozenset({"not_required", "conditional", "required"})
_RISK_LEVELS = frozenset({"low", "medium", "high"})


def _load_mapping(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        errors.append(f"{path.name}: invalid YAML: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{path.name}: top level must be a mapping")
        return {}
    version = payload.get("version")
    if not isinstance(version, str) or not _SEMVER_RE.fullmatch(version):
        errors.append(f"{path.name}: version must be semantic x.y.z")
    return payload


def domain_profile_path(root: Path, domain_profile: str) -> Path:
    if not _PROFILE_RE.fullmatch(domain_profile):
        raise ValidationError(
            f"invalid ontology domain profile: {domain_profile!r}"
        )
    path = root / "domains" / f"{domain_profile}.yaml"
    if not path.is_file():
        raise ValidationError(
            f"unknown ontology domain profile: {domain_profile}"
        )
    return path


def validate_ontology(
    root: Path,
    *,
    domain_profile: str = "research-project",
) -> list[str]:
    errors: list[str] = []
    all_payloads = {
        path: _load_mapping(path, errors) for path in sorted(root.rglob("*.yaml"))
    }
    required_paths = {
        "entities": root / "core/entity-types.yaml",
        "predicates": root / "core/predicates.yaml",
        "review": root / "policies/review-policy.yaml",
    }
    try:
        required_paths["domain"] = domain_profile_path(root, domain_profile)
    except ValidationError as exc:
        errors.append(str(exc))
    payloads = {
        name: all_payloads.get(path, {})
        for name, path in required_paths.items()
    }
    payloads.setdefault("domain", {})
    for path in required_paths.values():
        if not path.is_file():
            errors.append(f"missing ontology contract: {path.relative_to(root)}")

    entity_types: dict[str, Any] = {}
    core_entity_names: frozenset[str] = frozenset()
    for payload_name in ("entities", "domain"):
        definitions = payloads[payload_name].get("entity_types", {})
        if not isinstance(definitions, dict):
            errors.append(f"{required_paths[payload_name].name}: entity_types must be a mapping")
            continue
        if payload_name == "domain":
            for entity_name in sorted(core_entity_names.intersection(definitions)):
                errors.append(
                    f"{required_paths['domain'].name}: entity type {entity_name} "
                    "redefines core entity type"
                )
        else:
            core_entity_names = frozenset(definitions)
        entity_types.update(definitions)
    for entity_name, definition in entity_types.items():
        if not isinstance(definition, dict):
            errors.append(f"entity {entity_name}: definition must be a mapping")
            continue
        parent = definition.get("parent")
        if parent is not None and parent not in entity_types:
            errors.append(f"entity {entity_name}: unknown parent {parent}")

    predicates = payloads["predicates"].get("predicates", {})
    if not isinstance(predicates, dict):
        errors.append("predicates.yaml: predicates must be a mapping")
        predicates = {}
    domain_predicates = payloads["domain"].get("predicates", {})
    if isinstance(domain_predicates, dict):
        for name in sorted(set(domain_predicates).intersection(predicates)):
            errors.append(
                f"{required_paths['domain'].name}: predicate {name} redefines core predicate"
            )
    for name, definition in predicates.items():
        if not isinstance(definition, dict):
            errors.append(f"predicate {name}: definition must be a mapping")
            continue
        for side in ("domain", "range"):
            values = definition.get(side)
            if not isinstance(values, list) or not values:
                errors.append(f"predicate {name}: {side} must be a non-empty list")
                continue
            unknown = sorted(set(values).difference(entity_types))
            if unknown:
                errors.append(f"predicate {name}: unknown {side} types {unknown}")
        if "inverse" not in definition:
            errors.append(f"predicate {name}: inverse must be explicit")
        inverse = definition.get("inverse")
        if inverse is not None and inverse not in predicates:
            errors.append(f"predicate {name}: unknown inverse {inverse}")
        if definition.get("extraction") not in _EXTRACTION_POLICIES:
            errors.append(f"predicate {name}: invalid extraction policy")
        if definition.get("review") not in _REVIEW_POLICIES:
            errors.append(f"predicate {name}: invalid review policy")
        if definition.get("risk") not in _RISK_LEVELS:
            errors.append(f"predicate {name}: invalid risk level")

    required_review = {
        name
        for name, definition in predicates.items()
        if isinstance(definition, dict) and definition.get("review") == "required"
    }
    policy_values = payloads["review"].get("human_review_required", {})
    policy_predicates = (
        set(policy_values.get("predicates", [])) if isinstance(policy_values, dict) else set()
    )
    if required_review != policy_predicates:
        errors.append(
            "review-policy.yaml: predicates must exactly match required predicate reviews"
        )

    for path in sorted((root / "sources").glob("*.yaml")):
        payload = all_payloads[path]
        source_object_types = payload.get("source_object_types", {})
        if isinstance(source_object_types, dict):
            for object_name, object_definition in source_object_types.items():
                if not isinstance(object_definition, dict):
                    continue
                parent = object_definition.get("parent")
                if parent is not None and parent not in entity_types:
                    errors.append(
                        f"{path.name}: source object type {object_name} unknown parent {parent}"
                    )
        for predicate in payload.get("deterministic_relations", []):
            definition = predicates.get(predicate)
            if definition is None:
                errors.append(f"{path.name}: unknown deterministic relation {predicate}")
            elif not str(definition.get("extraction", "")).startswith("deterministic"):
                errors.append(f"{path.name}: relation {predicate} is not deterministic")
    return errors



# Fail-closed floor used by stores when no ontology catalog is available.
#
# This is a floor, not a snapshot: the contract test pins it to be a SUBSET
# of the set derived from `ontology/core/predicates.yaml`
# (review == "required" or risk == "high"), and requires the shipped tree to
# still derive at least this set. Predicates released later via the ontology
# discovery approval flow (`kip.adapters.ontology.release`) always default to
# review == "required", so the derived set can only grow beyond this floor;
# this constant intentionally does not grow with it, since it is a hardcoded
# fallback baked into deployed code, not something a running process can
# refresh in place. Widen it only for a predicate that must be
# evidence-enforced even when the ontology catalog fails to load.
FALLBACK_EVIDENCE_REQUIRED_PREDICATES: frozenset[str] = frozenset(
    {
        "amends",
        "supersedes",
        "approves",
        "evidences",
        "responds_to",
        "records_decision",
    }
)


def _optional_str(definition: dict[str, Any], key: str) -> str | None:
    value = definition.get(key)
    return str(value) if isinstance(value, str) and value.strip() else None


def load_catalog(
    root: Path,
    *,
    domain_profile: str = "research-project",
) -> OntologyCatalog:
    """Read the YAML ontology tree at `root` into an :class:`OntologyCatalog`."""
    errors = validate_ontology(root, domain_profile=domain_profile)
    if errors:
        raise ValidationError("invalid ontology contract: " + "; ".join(errors))
    predicate_payload = yaml.safe_load(
        (root / "core/predicates.yaml").read_text(encoding="utf-8")
    )
    entity_payload = yaml.safe_load(
        (root / "core/entity-types.yaml").read_text(encoding="utf-8")
    )
    domain_payload = yaml.safe_load(
        domain_profile_path(root, domain_profile).read_text(encoding="utf-8")
    )
    entity_definitions = {
        **entity_payload["entity_types"],
        **domain_payload["entity_types"],
    }
    parents = {
        str(name): (
            str(definition["parent"])
            if isinstance(definition, dict) and definition.get("parent") is not None
            else None
        )
        for name, definition in entity_definitions.items()
    }
    predicate_specs = {
        str(name): PredicateSpec(
            name=str(name),
            domain=tuple(str(item) for item in definition["domain"]),
            range=tuple(str(item) for item in definition["range"]),
            risk=definition["risk"],
            review=definition["review"],
            extraction=str(definition["extraction"]),
            description=_optional_str(definition, "description"),
            label_ko=_optional_str(definition, "label_ko"),
            description_ko=_optional_str(definition, "description_ko"),
        )
        for name, definition in predicate_payload["predicates"].items()
    }
    entity_labels: dict[str, dict[str, str]] = {}
    for name, definition in entity_definitions.items():
        if not isinstance(definition, dict):
            continue
        labels = {
            key: value
            for key in ("label_ko", "description", "description_ko")
            if (value := _optional_str(definition, key)) is not None
        }
        if labels:
            entity_labels[str(name)] = labels
    return OntologyCatalog(
        domain_profile=domain_profile,
        version=f"core/{predicate_payload['version']}",
        predicates=frozenset(predicate_payload["predicates"]),
        entity_parents=parents,
        predicate_specs=predicate_specs,
        entity_labels=entity_labels,
    )
