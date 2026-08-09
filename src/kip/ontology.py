from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from kip.errors import ValidationError

_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
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


def validate_ontology(root: Path) -> list[str]:
    errors: list[str] = []
    all_payloads = {
        path: _load_mapping(path, errors) for path in sorted(root.rglob("*.yaml"))
    }
    required_paths = {
        "entities": root / "core/entity-types.yaml",
        "predicates": root / "core/predicates.yaml",
        "domain": root / "domains/research-project.yaml",
        "review": root / "policies/review-policy.yaml",
    }
    payloads = {
        name: all_payloads.get(path, {})
        for name, path in required_paths.items()
    }
    for path in required_paths.values():
        if not path.is_file():
            errors.append(f"missing ontology contract: {path.relative_to(root)}")

    entity_types: dict[str, Any] = {}
    for payload_name in ("entities", "domain"):
        definitions = payloads[payload_name].get("entity_types", {})
        if not isinstance(definitions, dict):
            errors.append(f"{required_paths[payload_name].name}: entity_types must be a mapping")
            continue
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
        name for name, definition in predicates.items() if definition.get("review") == "required"
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
        for predicate in payload.get("deterministic_relations", []):
            definition = predicates.get(predicate)
            if definition is None:
                errors.append(f"{path.name}: unknown deterministic relation {predicate}")
            elif not str(definition.get("extraction", "")).startswith("deterministic"):
                errors.append(f"{path.name}: relation {predicate} is not deterministic")
    return errors


@dataclass(frozen=True, slots=True)
class PredicateSpec:
    name: str
    domain: tuple[str, ...]
    range: tuple[str, ...]
    risk: Literal["low", "medium", "high"]
    review: Literal["not_required", "conditional", "required"]
    extraction: str


@dataclass(frozen=True, slots=True)
class OntologyCatalog:
    version: str
    predicates: frozenset[str]
    entity_parents: dict[str, str | None]
    predicate_specs: dict[str, PredicateSpec]

    @classmethod
    def load(cls, root: Path) -> OntologyCatalog:
        errors = validate_ontology(root)
        if errors:
            raise ValidationError("invalid ontology contract: " + "; ".join(errors))
        predicate_payload = yaml.safe_load(
            (root / "core/predicates.yaml").read_text(encoding="utf-8")
        )
        entity_payload = yaml.safe_load(
            (root / "core/entity-types.yaml").read_text(encoding="utf-8")
        )
        domain_payload = yaml.safe_load(
            (root / "domains/research-project.yaml").read_text(encoding="utf-8")
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
            )
            for name, definition in predicate_payload["predicates"].items()
        }
        return cls(
            version=f"core/{predicate_payload['version']}",
            predicates=frozenset(predicate_payload["predicates"]),
            entity_parents=parents,
            predicate_specs=predicate_specs,
        )

    def validate_candidate(self, predicate: str, ontology_version: str) -> None:
        if ontology_version != self.version:
            raise ValidationError(
                f"ontology version must be {self.version}, received {ontology_version}"
            )
        if predicate not in self.predicates:
            raise ValidationError(f"unknown ontology predicate: {predicate}")

    def validate_entity_type(self, entity_type: str) -> None:
        if entity_type not in self.entity_parents:
            raise ValidationError(f"unknown ontology entity type: {entity_type}")

    def is_a(self, entity_type: str, expected_type: str) -> bool:
        self.validate_entity_type(entity_type)
        current: str | None = entity_type
        visited: set[str] = set()
        while current is not None and current not in visited:
            if current == expected_type:
                return True
            visited.add(current)
            current = self.entity_parents.get(current)
        return False

    def validate_relation(
        self,
        *,
        subject_type: str,
        predicate: str,
        object_type: str | None,
    ) -> PredicateSpec:
        self.validate_candidate(predicate, self.version)
        self.validate_entity_type(subject_type)
        spec = self.predicate_specs[predicate]
        if not any(self.is_a(subject_type, allowed) for allowed in spec.domain):
            raise ValidationError(
                f"entity type {subject_type} is outside predicate {predicate} domain"
            )
        if object_type is None:
            raise ValidationError(
                f"predicate {predicate} requires an ontology entity in its range"
            )
        self.validate_entity_type(object_type)
        if not any(self.is_a(object_type, allowed) for allowed in spec.range):
            raise ValidationError(
                f"entity type {object_type} is outside predicate {predicate} range"
            )
        return spec
