from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from kip.evaluation.answers import AnswerReview
from kip.evaluation.ontology import OntologyReview


class ReviewBundleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VariantReviews(ReviewBundleModel):
    answer: tuple[AnswerReview, ...] = ()
    ontology: tuple[OntologyReview, ...] = ()

    @model_validator(mode="after")
    def case_ids_are_unique_per_dimension(self) -> VariantReviews:
        for reviews in (self.answer, self.ontology):
            case_ids = [review.case_id for review in reviews]
            if len(case_ids) != len(set(case_ids)):
                raise ValueError("review case IDs must be unique per dimension")
        return self


class EvaluationReviewBundle(ReviewBundleModel):
    schema_version: Literal["kip.evaluation-review-bundle.v1"] = "kip.evaluation-review-bundle.v1"
    dataset_name: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    dataset_source_revision: str = Field(min_length=1)
    variants: dict[
        Literal["lexical", "vector", "hybrid", "reranked"],
        VariantReviews,
    ] = Field(min_length=1)


def load_review_bundle(path: Path) -> EvaluationReviewBundle:
    return EvaluationReviewBundle.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
