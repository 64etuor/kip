"""Read an ontology tree into the domain's :class:`OntologyRelease`.

Only the file shapes and the reading live here; what a release *means* is
:mod:`kip.domain.ontology`.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field

from kip.domain.ontology import (
    EntityDefinition,
    OntologyModel,
    OntologyRelease,
    PredicateDefinition,
)
from kip.errors import ValidationError
from kip.ontology import domain_profile_path, validate_ontology


class EntityFile(OntologyModel):
    ontology: str
    version: str
    entity_types: dict[str, EntityDefinition]
    controlled_values: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class PredicateFile(OntologyModel):
    ontology: str
    version: str
    predicates: dict[str, PredicateDefinition]


def load_release(
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
    return OntologyRelease(
        domain_profile=domain_profile,
        version=f"core/{predicates.version}",
        entities=core.entity_types | domain.entity_types,
        predicates=predicates.predicates,
    )
