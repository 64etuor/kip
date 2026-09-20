"""Ontology meaning as data: what a loaded ontology release says.

`ontology/` owns meaning, so the vocabulary it defines — entity types,
predicates and the rules that bind them — is domain data, not infrastructure.
Reading the YAML tree that carries it is a filesystem concern and lives behind
:mod:`kip.ports.ontology`; everything here is pure and immutable, so a use case
can hold an :class:`OntologyCatalog` without reaching for a file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from kip.errors import ValidationError


class OntologyModel(BaseModel):
    """Base for the released-ontology models: frozen, no unexpected keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EntityDefinition(OntologyModel):
    parent: str | None = None
    description: str | None = None
    abstract: bool = False
    # Presentation metadata; never part of release-diff semantics.
    label_ko: str | None = None
    description_ko: str | None = None


class PredicateDefinition(OntologyModel):
    domain: frozenset[str]
    range: frozenset[str]
    inverse: str | None
    risk: Literal["low", "medium", "high"]
    extraction: str
    review: Literal["not_required", "conditional", "required"]
    # Presentation metadata; never part of release-diff semantics.
    description: str | None = None
    label_ko: str | None = None
    description_ko: str | None = None


class OntologyRelease(OntologyModel):
    """The entity types and predicates one ontology tree releases."""

    domain_profile: str
    version: str
    entities: dict[str, EntityDefinition]
    predicates: dict[str, PredicateDefinition]


@dataclass(frozen=True, slots=True)
class PredicateSpec:
    name: str
    domain: tuple[str, ...]
    range: tuple[str, ...]
    risk: Literal["low", "medium", "high"]
    review: Literal["not_required", "conditional", "required"]
    extraction: str
    description: str | None = None
    label_ko: str | None = None
    description_ko: str | None = None


@dataclass(frozen=True, slots=True)
class OntologyCatalog:
    domain_profile: str
    version: str
    predicates: frozenset[str]
    entity_parents: dict[str, str | None]
    predicate_specs: dict[str, PredicateSpec]
    entity_labels: dict[str, dict[str, str]] = field(default_factory=dict)

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

    def evidence_required_predicates(self) -> frozenset[str]:
        """Predicates whose approval must fail closed without exact evidence.

        Derived from the loaded catalog (`review == "required"` or
        `risk == "high"`) so the enforcement set cannot diverge from
        `ontology/core/predicates.yaml`.
        """
        return frozenset(
            name
            for name, spec in self.predicate_specs.items()
            if spec.review == "required" or spec.risk == "high"
        )

    def mining_contract(self) -> dict[str, object]:
        return {
            "version": self.version,
            "domain_profile": self.domain_profile,
            "entity_types": sorted(self.entity_parents),
            "entity_type_labels": {
                name: dict(labels)
                for name, labels in sorted(self.entity_labels.items())
            },
            "predicates": {
                name: {
                    "domain": list(spec.domain),
                    "range": list(spec.range),
                    "risk": spec.risk,
                    "review": spec.review,
                    "extraction": spec.extraction,
                    **(
                        {"description": spec.description}
                        if spec.description is not None
                        else {}
                    ),
                    **(
                        {"label_ko": spec.label_ko}
                        if spec.label_ko is not None
                        else {}
                    ),
                    **(
                        {"description_ko": spec.description_ko}
                        if spec.description_ko is not None
                        else {}
                    ),
                }
                for name, spec in sorted(self.predicate_specs.items())
            },
        }
