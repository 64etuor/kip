from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _display_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def normalize_entity_name(value: str) -> str:
    return _display_name(value).casefold()


class KnowledgeEntity(KnowledgeModel):
    id: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    canonical_name_normalized: str = ""
    aliases: list[str] = Field(default_factory=list)
    status: Literal["active", "merged", "retired"] = "active"
    acl_scopes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_names(self) -> Self:
        display = _display_name(self.canonical_name)
        if not display:
            raise ValueError("canonical entity name cannot be blank")
        aliases: list[str] = []
        seen = {normalize_entity_name(display)}
        for raw_alias in self.aliases:
            alias = _display_name(raw_alias)
            normalized = normalize_entity_name(alias)
            if alias and normalized not in seen:
                seen.add(normalized)
                aliases.append(alias)
        object.__setattr__(self, "canonical_name", display)
        object.__setattr__(self, "canonical_name_normalized", normalize_entity_name(display))
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "acl_scopes", sorted(set(self.acl_scopes)))
        return self


class EntityIdentifier(KnowledgeModel):
    id: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    value_display: str = Field(min_length=1)
    value_normalized: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    verified: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateEvidence(KnowledgeModel):
    content_unit_id: str = Field(min_length=1)
    source_revision_sha256: str | None = None
    locator: dict[str, Any] = Field(default_factory=dict)
    quote_hash: str | None = None


class RelationDerivation(KnowledgeModel):
    kind: Literal["manual", "deterministic", "model", "ontology_migration"]
    name: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    model: str | None = None
    run_id: str | None = None


class RelationProposal(KnowledgeModel):
    subject_id: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object_entity_id: str | None = None
    object_value: Any = None
    ontology_version: str = Field(min_length=1)
    evidence_unit_ids: tuple[str, ...] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    derivation: RelationDerivation

    @field_validator("evidence_unit_ids")
    @classmethod
    def unique_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("evidence unit IDs must be unique")
        return values

    @model_validator(mode="after")
    def valid_object_and_interval(self) -> Self:
        if (self.object_entity_id is None) == (self.object_value is None):
            raise ValueError(
                "exactly one of object_entity_id or object_value is required"
            )
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("valid_to must be later than valid_from")
        return self


def relation_candidate_fingerprint(
    *,
    proposal: RelationProposal,
    subject: KnowledgeEntity,
    object_entity: KnowledgeEntity | None,
    evidence: tuple[CandidateEvidence, ...],
) -> str:
    payload = {
        "ontology_version": proposal.ontology_version,
        "subject": {
            "type": subject.entity_type,
            "name": subject.canonical_name_normalized,
        },
        "predicate": proposal.predicate,
        "object": (
            {
                "type": object_entity.entity_type,
                "name": object_entity.canonical_name_normalized,
            }
            if object_entity is not None
            else proposal.object_value
        ),
        "evidence": [
            item.model_dump(mode="json")
            for item in sorted(evidence, key=lambda value: value.content_unit_id)
        ],
        "valid_from": proposal.valid_from.isoformat() if proposal.valid_from else None,
        "valid_to": proposal.valid_to.isoformat() if proposal.valid_to else None,
        "derivation": proposal.derivation.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def intervals_overlap(
    left_from: datetime | None,
    left_to: datetime | None,
    right_from: datetime | None,
    right_to: datetime | None,
) -> bool:
    return (left_to is None or right_from is None or right_from < left_to) and (
        right_to is None or left_from is None or left_from < right_to
    )


def stable_candidate_id(fingerprint: str) -> str:
    digest = re.sub(r"^sha256:", "", fingerprint)
    return f"cand_{digest[:32]}"
