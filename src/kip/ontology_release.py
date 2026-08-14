from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from kip.errors import ValidationError
from kip.ontology import domain_profile_path, validate_ontology


class OntologyModel(BaseModel):
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


class EntityFile(OntologyModel):
    ontology: str
    version: str
    entity_types: dict[str, EntityDefinition]
    controlled_values: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class PredicateFile(OntologyModel):
    ontology: str
    version: str
    predicates: dict[str, PredicateDefinition]


class OntologyRelease(OntologyModel):
    domain_profile: str
    version: str
    entities: dict[str, EntityDefinition]
    predicates: dict[str, PredicateDefinition]

    @classmethod
    def load(
        cls,
        root: Path,
        *,
        domain_profile: str = "research-project",
    ) -> OntologyRelease:
        errors = validate_ontology(root, domain_profile=domain_profile)
        if errors:
            raise ValidationError("invalid ontology contract: " + "; ".join(errors))
        core = EntityFile.model_validate(
            yaml.safe_load((root / "core/entity-types.yaml").read_text(encoding="utf-8"))
        )
        domain = EntityFile.model_validate(
            yaml.safe_load(
                domain_profile_path(root, domain_profile).read_text(encoding="utf-8")
            )
        )
        predicates = PredicateFile.model_validate(
            yaml.safe_load((root / "core/predicates.yaml").read_text(encoding="utf-8"))
        )
        return cls(
            domain_profile=domain_profile,
            version=f"core/{predicates.version}",
            entities=core.entity_types | domain.entity_types,
            predicates=predicates.predicates,
        )
