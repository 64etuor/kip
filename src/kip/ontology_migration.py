from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, model_validator

from kip.errors import ValidationError
from kip.ontology_release import OntologyModel, OntologyRelease


class ChangeClassification(StrEnum):
    COMPATIBLE = "compatible"
    REVIEW_REQUIRED = "review_required"
    BREAKING = "breaking"


class SymbolKind(StrEnum):
    ENTITY = "entity"
    PREDICATE = "predicate"


class OntologyChange(OntologyModel):
    symbol_kind: SymbolKind
    symbol: str
    change_type: str
    classification: ChangeClassification


class OntologyDiff(OntologyModel):
    schema_version: Literal["kip.ontology-diff.v1"] = "kip.ontology-diff.v1"
    from_version: str
    to_version: str
    classification: ChangeClassification
    changes: tuple[OntologyChange, ...]
    before_entities: frozenset[str] = Field(exclude=True)
    after_entities: frozenset[str] = Field(exclude=True)
    before_predicates: frozenset[str] = Field(exclude=True)
    after_predicates: frozenset[str] = Field(exclude=True)


class MigrationOperation(OntologyModel):
    operation: Literal["rename", "deprecate", "replace", "split", "merge"]
    symbol_kind: SymbolKind
    sources: tuple[str, ...] = Field(min_length=1)
    targets: tuple[str, ...] = ()
    review_required: bool

    @model_validator(mode="after")
    def targets_match_operation(self) -> Self:
        if len(self.sources) != len(set(self.sources)):
            raise ValueError("migration operation sources must be unique")
        if len(self.targets) != len(set(self.targets)):
            raise ValueError("migration operation targets must be unique")
        source_count = len(self.sources)
        target_count = len(self.targets)
        match self.operation:
            case "rename" | "replace":
                if source_count != 1 or target_count != 1:
                    raise ValueError(
                        f"{self.operation} requires exactly one source and one target"
                    )
            case "split":
                if source_count != 1 or target_count < 2:
                    raise ValueError(
                        "split requires exactly one source and at least two targets"
                    )
            case "merge":
                if source_count < 2 or target_count != 1:
                    raise ValueError(
                        "merge requires at least two sources and exactly one target"
                    )
            case "deprecate":
                if target_count:
                    raise ValueError("deprecate does not accept targets")
        return self


class OntologyMigration(OntologyModel):
    schema_version: Literal["kip.ontology-migration.v1"]
    from_version: str
    to_version: str
    operations: tuple[MigrationOperation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def versions_must_advance(self) -> Self:
        if self.from_version == self.to_version:
            raise ValueError("ontology migration versions must differ")
        return self


class OntologyMigrationMaterialization(OntologyModel):
    schema_version: Literal["kip.ontology-migration-materialization.v1"] = (
        "kip.ontology-migration-materialization.v1"
    )
    from_version: str
    to_version: str
    migration_sha256: str
    source_assertion_count: int = Field(ge=0)
    created_candidate_count: int = Field(ge=0)
    existing_candidate_count: int = Field(ge=0)
    deprecated_assertion_count: int = Field(ge=0)
    candidate_ids: list[str] = Field(default_factory=list)


_CLASSIFICATION_RANK = {
    ChangeClassification.COMPATIBLE: 0,
    ChangeClassification.REVIEW_REQUIRED: 1,
    ChangeClassification.BREAKING: 2,
}
_RISK_RANK = {"low": 0, "medium": 1, "high": 2}
_REVIEW_RANK = {"not_required": 0, "conditional": 1, "required": 2}


def _change(
    kind: SymbolKind,
    symbol: str,
    change_type: str,
    classification: ChangeClassification,
) -> OntologyChange:
    return OntologyChange(
        symbol_kind=kind,
        symbol=symbol,
        change_type=change_type,
        classification=classification,
    )


def _set_change(
    kind: SymbolKind,
    symbol: str,
    field: str,
    before: frozenset[str],
    after: frozenset[str],
) -> OntologyChange | None:
    if before == after:
        return None
    if after < before:
        suffix = "narrowed"
        classification = ChangeClassification.BREAKING
    elif after > before:
        suffix = "widened"
        classification = ChangeClassification.REVIEW_REQUIRED
    else:
        suffix = "changed"
        classification = ChangeClassification.BREAKING
    return _change(kind, symbol, f"predicate_{field}_{suffix}", classification)


def _rank_change(
    symbol: str,
    field: str,
    before: str,
    after: str,
    ranks: dict[str, int],
) -> OntologyChange | None:
    if before == after:
        return None
    direction = "strengthened" if ranks[after] > ranks[before] else "weakened"
    classification = (
        ChangeClassification.REVIEW_REQUIRED
        if direction == "strengthened"
        else ChangeClassification.BREAKING
    )
    return _change(
        SymbolKind.PREDICATE,
        symbol,
        f"predicate_{field}_{direction}",
        classification,
    )


def _entity_changes(before: OntologyRelease, after: OntologyRelease) -> list[OntologyChange]:
    changes = [
        _change(SymbolKind.ENTITY, name, "entity_added", ChangeClassification.COMPATIBLE)
        for name in sorted(after.entities.keys() - before.entities.keys())
    ]
    changes.extend(
        _change(SymbolKind.ENTITY, name, "entity_removed", ChangeClassification.BREAKING)
        for name in sorted(before.entities.keys() - after.entities.keys())
    )
    for name in sorted(before.entities.keys() & after.entities.keys()):
        if before.entities[name].parent != after.entities[name].parent:
            changes.append(
                _change(
                    SymbolKind.ENTITY,
                    name,
                    "entity_parent_changed",
                    ChangeClassification.BREAKING,
                )
            )
    return changes


def _predicate_changes(before: OntologyRelease, after: OntologyRelease) -> list[OntologyChange]:
    changes = [
        _change(
            SymbolKind.PREDICATE,
            name,
            "predicate_added",
            ChangeClassification.COMPATIBLE,
        )
        for name in sorted(after.predicates.keys() - before.predicates.keys())
    ]
    changes.extend(
        _change(
            SymbolKind.PREDICATE,
            name,
            "predicate_removed",
            ChangeClassification.BREAKING,
        )
        for name in sorted(before.predicates.keys() - after.predicates.keys())
    )
    for name in sorted(before.predicates.keys() & after.predicates.keys()):
        old = before.predicates[name]
        new = after.predicates[name]
        candidates = (
            _set_change(SymbolKind.PREDICATE, name, "domain", old.domain, new.domain),
            _set_change(SymbolKind.PREDICATE, name, "range", old.range, new.range),
            _rank_change(name, "risk", old.risk, new.risk, _RISK_RANK),
            _rank_change(name, "review", old.review, new.review, _REVIEW_RANK),
        )
        changes.extend(change for change in candidates if change is not None)
        if old.inverse != new.inverse or old.extraction != new.extraction:
            changes.append(
                _change(
                    SymbolKind.PREDICATE,
                    name,
                    "predicate_semantics_changed",
                    ChangeClassification.REVIEW_REQUIRED,
                )
            )
    return changes


def diff_ontologies(
    before_root: Path,
    after_root: Path,
    *,
    before_domain_profile: str = "research-project",
    after_domain_profile: str = "research-project",
) -> OntologyDiff:
    before = OntologyRelease.load(
        before_root,
        domain_profile=before_domain_profile,
    )
    after = OntologyRelease.load(
        after_root,
        domain_profile=after_domain_profile,
    )
    changes = tuple(_entity_changes(before, after) + _predicate_changes(before, after))
    classification = max(
        (change.classification for change in changes),
        key=_CLASSIFICATION_RANK.__getitem__,
        default=ChangeClassification.COMPATIBLE,
    )
    return OntologyDiff(
        from_version=before.version,
        to_version=after.version,
        classification=classification,
        changes=changes,
        before_entities=frozenset(before.entities),
        after_entities=frozenset(after.entities),
        before_predicates=frozenset(before.predicates),
        after_predicates=frozenset(after.predicates),
    )


def load_migration(path: Path) -> OntologyMigration:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValidationError(f"cannot load ontology migration: {error}") from error
    return OntologyMigration.model_validate(payload)


def ontology_migration_sha256(migration: OntologyMigration) -> str:
    payload = json.dumps(
        migration.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def validate_migration_coverage(
    diff: OntologyDiff,
    migration: OntologyMigration | None,
) -> list[str]:
    breaking = [
        change for change in diff.changes if change.classification is ChangeClassification.BREAKING
    ]
    if migration is None:
        return (
            ["breaking ontology changes require a migration manifest"]
            if breaking
            else []
        )
    errors: list[str] = []
    if migration.from_version != diff.from_version or migration.to_version != diff.to_version:
        errors.append("migration versions do not match ontology diff")
    covered = {
        (operation.symbol_kind, source)
        for operation in migration.operations
        for source in operation.sources
    }
    seen_sources: set[tuple[SymbolKind, str]] = set()
    for operation in migration.operations:
        for source in operation.sources:
            key = (operation.symbol_kind, source)
            if key in seen_sources:
                errors.append(
                    f"duplicate migration source {operation.symbol_kind.value} {source}"
                )
            seen_sources.add(key)
    for change in breaking:
        if (change.symbol_kind, change.symbol) not in covered:
            errors.append(f"uncovered breaking {change.symbol_kind.value} {change.symbol}")
    for operation in migration.operations:
        match operation.symbol_kind:
            case SymbolKind.ENTITY:
                before_symbols = diff.before_entities
                after_symbols = diff.after_entities
            case SymbolKind.PREDICATE:
                before_symbols = diff.before_predicates
                after_symbols = diff.after_predicates
        for source in operation.sources:
            if source not in before_symbols:
                errors.append(f"unknown source {operation.symbol_kind.value} {source}")
        for target in operation.targets:
            if target not in after_symbols:
                errors.append(f"unknown target {operation.symbol_kind.value} {target}")
    return errors
