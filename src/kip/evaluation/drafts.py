"""Judge-proposed golden case drafts and the human sample-audit promotion gate.

An LLM judge may PROPOSE evaluation cases as a ``kip.golden-draft.v1`` document,
but it can never create canonical truth on its own (see
``docs/RAG_EVALUATION.md``: "an LLM judge cannot create canonical truth or
auto-promote"). A human reviewer samples the draft, records approve/reject
decisions in a ``kip.golden-draft-review.v1`` document bound to the exact
draft content by a sha256 fingerprint, and only an explicit, fail-closed
``promote`` step appends cases into a real ``kip.golden-dataset.v1`` file.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Literal, cast, get_args

import yaml
from pydantic import Field, model_validator

from kip.errors import ValidationError
from kip.evaluation.models import EvaluationModel, GoldenCase, GoldenDataset

JudgeKind = Literal["external_agent", "generation_model"]
DraftReviewAction = Literal["approve", "reject"]

# The canonical-authority literal accepted at the golden-dataset level (see
# `GoldenDataset.lifecycle` in kip.evaluation.models). Read dynamically off
# the model field so this never drifts from the real contract.
_DATASET_LIFECYCLE_VALUES: tuple[str, ...] = get_args(GoldenDataset.model_fields["lifecycle"].annotation)

# Fields on `GoldenCase` that only a human promotion decision may set. A
# judge draft proposes content; it never gets to self-declare that its own
# proposal is already reviewed, versioned, attributed, or pinned to a source
# revision — that would forge reviewer identity and gate eligibility.
_CANONICAL_AUTHORITY_DEFAULTS: dict[str, Any] = {
    "lifecycle": "draft",
    "version": "draft",
    "reviewer": None,
    "source_revision": None,
}


class JudgeProvenance(EvaluationModel):
    judge_kind: JudgeKind
    model: str | None = None
    revision: str | None = None
    notes: str | None = None


class GoldenDraftCase(GoldenCase):
    """A judge-proposed case: the golden case shape plus judge provenance.

    Drafts carry no ``status`` field — they are immutable proposals. A case
    only becomes canonical truth through explicit human promotion.
    """

    judge_confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=500)

    # `mode="before"` so this runs on the raw input dict ahead of *any*
    # field-level or base-class (`GoldenCase.reviewed_cases_are_immutable`)
    # validation, and always reports the specific, unambiguous reason —
    # rather than racing a base-class consistency check that would otherwise
    # sometimes fire first with an unrelated message.
    @model_validator(mode="before")
    @classmethod
    def no_canonical_authority_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        offending_fields = sorted(
            field
            for field, default in _CANONICAL_AUTHORITY_DEFAULTS.items()
            if field in data and data[field] != default
        )
        if offending_fields:
            case_id = data.get("id", "<unknown>")
            raise ValueError(
                f"case '{case_id}' sets canonical-authority field(s) {offending_fields}: "
                "a judge proposal may not set canonical-authority fields"
            )
        return data


class GoldenDraft(EvaluationModel):
    schema_version: Literal["kip.golden-draft.v1"] = "kip.golden-draft.v1"
    name: str = Field(min_length=1)
    description: str | None = None
    corpus_fingerprint: str | None = None
    judge: JudgeProvenance
    cases: list[GoldenDraftCase] = Field(min_length=1)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> GoldenDraft:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("draft case ids must be unique")
        return self


class DraftReviewDecision(EvaluationModel):
    case_id: str = Field(min_length=1)
    action: DraftReviewAction
    note: str | None = None


class GoldenDraftReview(EvaluationModel):
    schema_version: Literal["kip.golden-draft-review.v1"] = "kip.golden-draft-review.v1"
    draft_name: str = Field(min_length=1)
    draft_fingerprint: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    decisions: list[DraftReviewDecision] = Field(default_factory=list)

    @model_validator(mode="after")
    def decisions_are_unique_per_case(self) -> GoldenDraftReview:
        ids = [decision.case_id for decision in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("draft review decisions must be unique per case id")
        return self


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via write-temp-then-``os.replace``.

    Mirrors `kip.ontology_discovery_release._atomic_write`: a reader never
    observes a partially written draft-review or dataset file, and a crash
    mid-write leaves the original file untouched.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def compute_draft_fingerprint(draft_bytes: bytes) -> str:
    return "sha256:" + hashlib.sha256(draft_bytes).hexdigest()


def load_draft(path: Path) -> GoldenDraft:
    return GoldenDraft.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_draft_review(path: Path) -> GoldenDraftReview:
    return GoldenDraftReview.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def validate_draft(path: Path) -> dict[str, Any]:
    draft_bytes = path.read_bytes()
    draft = GoldenDraft.model_validate(yaml.safe_load(draft_bytes))
    return {
        "draft": draft.name,
        "case_count": len(draft.cases),
        "categories": sorted({case.category for case in draft.cases}),
        "judge_kind": draft.judge.judge_kind,
        "fingerprint": compute_draft_fingerprint(draft_bytes),
    }


def record_draft_review_decision(
    *,
    draft_path: Path,
    review_path: Path,
    case_id: str,
    action: str,
    reviewer: str,
    note: str | None = None,
) -> dict[str, Any]:
    draft_bytes = draft_path.read_bytes()
    draft = GoldenDraft.model_validate(yaml.safe_load(draft_bytes))
    if case_id not in {case.id for case in draft.cases}:
        raise ValidationError(f"case '{case_id}' is not present in draft '{draft.name}'")
    if action not in get_args(DraftReviewAction):
        raise ValidationError(f"action must be one of {get_args(DraftReviewAction)}, got '{action}'")
    fingerprint = compute_draft_fingerprint(draft_bytes)

    if review_path.exists():
        existing_review = load_draft_review(review_path)
        if existing_review.draft_fingerprint != fingerprint:
            raise ValidationError(
                "review is bound to a different draft fingerprint; "
                "re-review the current draft content before recording decisions"
            )
        decisions = [decision for decision in existing_review.decisions if decision.case_id != case_id]
    else:
        decisions = []

    decisions.append(
        DraftReviewDecision(case_id=case_id, action=cast(DraftReviewAction, action), note=note)
    )
    review = GoldenDraftReview(
        draft_name=draft.name,
        draft_fingerprint=fingerprint,
        reviewer=reviewer,
        decisions=decisions,
    )
    review_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        review_path,
        yaml.safe_dump(review.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
    )
    return {
        "draft": draft.name,
        "review": str(review_path),
        "case_id": case_id,
        "action": action,
        "reviewer": reviewer,
        "decision_count": len(review.decisions),
        "draft_fingerprint": fingerprint,
    }


def _promoted_case_payload(
    case: GoldenDraftCase,
    marker: str,
    *,
    lifecycle: str,
    version: str,
    reviewer: str,
    source_revision: str,
) -> dict[str, Any]:
    payload = case.model_dump(
        mode="json",
        exclude={"judge_confidence", "rationale"},
        exclude_defaults=True,
    )
    existing_notes = payload.get("notes")
    payload["notes"] = f"{existing_notes} | {marker}" if existing_notes else marker
    # Authority is assigned by promotion, never by the judge draft: the
    # `no_canonical_authority_fields` validator above guarantees the draft
    # case still carries the untouched defaults at this point.
    payload["lifecycle"] = lifecycle
    payload["version"] = version
    payload["reviewer"] = reviewer
    payload["source_revision"] = source_revision
    # Round-trip through the real golden-dataset case model: this is the
    # contract the appended case must satisfy, not merely "close enough".
    GoldenCase.model_validate(payload)
    return payload


def promote_draft(
    *,
    draft_path: Path,
    review_path: Path,
    dataset_path: Path,
    min_sample_rate: float = 0.2,
    lifecycle: str = "reviewed",
    dataset_version: str | None = None,
    source_revision: str | None = None,
) -> dict[str, Any]:
    draft_bytes = draft_path.read_bytes()
    draft = GoldenDraft.model_validate(yaml.safe_load(draft_bytes))
    fingerprint = compute_draft_fingerprint(draft_bytes)

    review = load_draft_review(review_path)
    if review.draft_fingerprint != fingerprint:
        raise ValidationError(
            "review does not bind to the current draft content; re-review before promoting"
        )
    if review.draft_name != draft.name:
        raise ValidationError("review draft_name does not match the promoted draft")
    if not review.reviewer.strip():
        raise ValidationError("promotion requires a non-empty reviewer")

    if lifecycle == "draft" or lifecycle not in _DATASET_LIFECYCLE_VALUES:
        raise ValidationError(
            f"--lifecycle must be one of {sorted(set(_DATASET_LIFECYCLE_VALUES) - {'draft'})}, "
            f"got {lifecycle!r}"
        )
    if dataset_version is not None and dataset_version == "draft":
        raise ValidationError("--dataset-version must not be 'draft'")
    if source_revision is not None and not source_revision.strip():
        raise ValidationError("--source-revision must be non-empty")

    draft_case_ids = {case.id for case in draft.cases}
    unknown_case_ids = {decision.case_id for decision in review.decisions} - draft_case_ids
    if unknown_case_ids:
        raise ValidationError(
            f"review references case ids not present in the draft: {sorted(unknown_case_ids)}"
        )

    total_case_count = len(draft.cases)
    sample_rate = len(review.decisions) / total_case_count
    if sample_rate < min_sample_rate:
        raise ValidationError(
            f"sampled coverage {sample_rate:.3f} is below the minimum sample rate "
            f"{min_sample_rate:.3f}; review more cases before promoting"
        )

    rejected = sorted(decision.case_id for decision in review.decisions if decision.action == "reject")
    if rejected:
        raise ValidationError(
            "promotion refused: sampled review rejected case(s) "
            f"{rejected}; triage the draft batch instead of promoting around a rejection"
        )

    is_fresh_dataset = not dataset_path.exists()
    raw_dataset: dict[str, Any]
    existing_dataset_version: str | None
    if not is_fresh_dataset:
        raw_dataset = yaml.safe_load(dataset_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw_dataset, dict):
            raise ValidationError(f"target dataset '{dataset_path}' is not a valid golden dataset document")
        existing_dataset_version = raw_dataset.get("version") or "draft"
    else:
        raw_dataset = {"schema_version": "kip.golden-dataset.v1", "name": draft.name}
        if draft.description is not None:
            raw_dataset["description"] = draft.description
        if draft.corpus_fingerprint is not None:
            raw_dataset["corpus_fingerprint"] = draft.corpus_fingerprint
        raw_dataset["cases"] = []
        existing_dataset_version = None

    existing_cases = list(raw_dataset.get("cases") or [])
    existing_case_ids = {case.get("id") for case in existing_cases}
    collisions = sorted(draft_case_ids & existing_case_ids)
    if collisions:
        raise ValidationError(
            f"promotion refused: case id collision with existing dataset '{dataset_path}': {collisions}"
        )

    # Resolve the canonical-authority fields this promotion assigns. These
    # are never read from the judge draft (see `no_canonical_authority_fields`
    # on `GoldenDraftCase`); they come only from explicit promotion inputs,
    # falling back to the target dataset's own non-draft version.
    if dataset_version is not None:
        resolved_dataset_version = dataset_version
    elif existing_dataset_version is not None and existing_dataset_version != "draft":
        resolved_dataset_version = existing_dataset_version
    else:
        raise ValidationError(
            "promotion requires --dataset-version: the target dataset has no "
            "non-draft version to default to"
        )

    if source_revision is not None:
        resolved_source_revision = source_revision
    elif draft.corpus_fingerprint:
        resolved_source_revision = draft.corpus_fingerprint
    else:
        raise ValidationError(
            "promotion requires --source-revision: the draft has no corpus_fingerprint to default to"
        )

    marker = f"judge-proposed ({draft.judge.judge_kind}), sample-audited by {review.reviewer}"
    promoted_payloads = [
        _promoted_case_payload(
            case,
            marker,
            lifecycle=lifecycle,
            version=resolved_dataset_version,
            reviewer=review.reviewer,
            source_revision=resolved_source_revision,
        )
        for case in draft.cases
    ]
    raw_dataset["cases"] = existing_cases + promoted_payloads

    if is_fresh_dataset:
        raw_dataset["lifecycle"] = lifecycle
        raw_dataset["version"] = resolved_dataset_version
        raw_dataset["reviewer"] = review.reviewer
        raw_dataset["source_revision"] = resolved_source_revision

    # Confirm the whole rewritten file still satisfies the real dataset
    # contract used by `evaluate validate` / the runner before we persist it.
    validated_dataset = GoldenDataset.model_validate(raw_dataset)

    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        dataset_path,
        yaml.safe_dump(raw_dataset, allow_unicode=True, sort_keys=False),
    )

    return {
        "dataset": str(dataset_path),
        "dataset_name": validated_dataset.name,
        "promoted_case_count": len(promoted_payloads),
        "total_dataset_case_count": len(validated_dataset.cases),
        "total_draft_case_count": total_case_count,
        "reviewed_case_count": len(review.decisions),
        "sample_rate": sample_rate,
        "reviewer": review.reviewer,
        "judge_kind": draft.judge.judge_kind,
        "lifecycle": lifecycle,
        "dataset_version": resolved_dataset_version,
        "source_revision": resolved_source_revision,
    }
