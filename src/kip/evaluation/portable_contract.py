from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from kip.evaluation.models import GoldenCase, GoldenDataset


class PortableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PortableQuestions(PortableModel):
    exact_identifier: str = Field(min_length=1)
    natural_language: str = Field(min_length=1)
    terse: str = Field(min_length=1)
    code_switch: str = Field(min_length=1)
    typo_noise: str = Field(min_length=1)


class PortableDocument(PortableModel):
    id: str = Field(min_length=2)
    document_id: str = Field(min_length=2)
    code: str = Field(min_length=2)
    title: str = Field(min_length=1)
    body: str = Field(min_length=20)
    questions: PortableQuestions


class PortableSuite(PortableModel):
    schema_version: str = "kip.portable-golden.v1"
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    acl_scope: str = Field(min_length=1)
    documents: list[PortableDocument] = Field(min_length=20)

    @model_validator(mode="after")
    def unique_contract_ids(self) -> PortableSuite:
        for values in (
            [document.id for document in self.documents],
            [document.document_id for document in self.documents],
            [document.code for document in self.documents],
        ):
            if len(values) != len(set(values)):
                raise PydanticCustomError(
                    "portable_id_collision",
                    "portable corpus IDs and codes must be unique",
                )
        return self


_QUESTION_FIELDS = (
    ("exact_identifier", "exact_identifier", "EXACT"),
    ("natural_language", "natural_language", "NATURAL"),
    ("terse", "terse_query", "TERSE"),
    ("code_switch", "code_switch", "CODE"),
    ("typo_noise", "typo_noise", "TYPO"),
)


def load_portable_suite(path: Path) -> PortableSuite:
    return PortableSuite.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def expand_portable_dataset(
    suite: PortableSuite,
    suite_bytes: bytes,
) -> GoldenDataset:
    cases: list[GoldenCase] = []
    for document in suite.documents:
        for field, category, suffix in _QUESTION_FIELDS:
            cases.append(
                GoldenCase(
                    id=f"{document.id}-{suffix}",
                    question=getattr(document.questions, field),
                    category=category,
                    principal="principal_portable",
                    acl_scopes=[suite.acl_scope],
                    expected_documents=[document.document_id],
                    recall_at=10,
                    lifecycle="reviewed",
                    version=suite.version,
                    reviewer=suite.reviewer,
                    source_revision=suite.source_revision,
                )
            )
        cases.append(
            GoldenCase(
                id=f"{document.id}-ACL",
                question=document.questions.exact_identifier,
                category="access_denied",
                principal="principal_portable",
                acl_scopes=["public"],
                expected_documents=[],
                forbidden_documents=[document.document_id],
                recall_at=10,
                lifecycle="reviewed",
                version=suite.version,
                reviewer=suite.reviewer,
                source_revision=suite.source_revision,
            )
        )
    return GoldenDataset(
        name=suite.name,
        description=suite.description,
        corpus_fingerprint="sha256:" + hashlib.sha256(suite_bytes).hexdigest(),
        lifecycle="reviewed",
        version=suite.version,
        reviewer=suite.reviewer,
        source_revision=suite.source_revision,
        cases=cases,
    )
