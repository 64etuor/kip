from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError as PydanticValidationError
from typer.testing import CliRunner

from kip.cli import app
from kip.errors import ValidationError
from kip.evaluation.drafts import (
    compute_draft_fingerprint,
    load_draft,
    load_draft_review,
    promote_draft,
    record_draft_review_decision,
    validate_draft,
)
from kip.evaluation.runner import load_dataset

ROOT = Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    return {
        "KIP_CONFIG": str(ROOT / "config/kip.example.toml"),
        "KIP_DATABASE_URL": "memory://",
        "KIP_PROJECT_ROOT": str(ROOT),
        "KIP_ENV": "test",
    }


def _case(case_id: str, *, confidence: float = 0.8) -> dict:
    return {
        "id": case_id,
        "question": f"질문 {case_id}",
        "category": "semantic_paraphrase",
        "principal": "principal_public",
        "acl_scopes": ["workspace:default"],
        "expected_documents": [f"ldoc_{case_id.lower()}"],
        "forbidden_documents": [],
        "recall_at": 10,
        "judge_confidence": confidence,
        "rationale": f"Proposed from corpus coverage gap around {case_id}.",
    }


def _draft(case_ids: list[str], *, name: str = "draft-fixture") -> dict:
    return {
        "schema_version": "kip.golden-draft.v1",
        "name": name,
        "description": "Fixture draft",
        "corpus_fingerprint": "sha256:fixture",
        "judge": {
            "judge_kind": "generation_model",
            "model": "judge-v1",
        },
        "cases": [_case(case_id) for case_id in case_ids],
    }


def _write_draft(path: Path, case_ids: list[str], **kwargs: object) -> Path:
    path.write_text(yaml.safe_dump(_draft(case_ids, **kwargs), sort_keys=False), encoding="utf-8")
    return path


def _write_review_file(
    path: Path,
    *,
    draft_name: str,
    draft_fingerprint: str,
    reviewer: str,
    decisions: list[dict],
) -> Path:
    payload = {
        "schema_version": "kip.golden-draft-review.v1",
        "draft_name": draft_name,
        "draft_fingerprint": draft_fingerprint,
        "reviewer": reviewer,
        "decisions": decisions,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


# --- draft validate -----------------------------------------------------


def test_validate_draft_ok(tmp_path: Path) -> None:
    draft_path = _write_draft(tmp_path / "draft.yaml", ["EX-001", "EX-002"])

    summary = validate_draft(draft_path)

    assert summary["case_count"] == 2
    assert summary["draft"] == "draft-fixture"
    assert summary["judge_kind"] == "generation_model"
    assert summary["fingerprint"] == compute_draft_fingerprint(draft_path.read_bytes())


def test_validate_draft_rejects_bad_schema(tmp_path: Path) -> None:
    draft_path = tmp_path / "draft.yaml"
    draft_path.write_text(
        yaml.safe_dump({"schema_version": "kip.golden-draft.v1", "name": "bad"}),
        encoding="utf-8",
    )

    with pytest.raises(PydanticValidationError):
        validate_draft(draft_path)


def test_validate_draft_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    draft = _draft(["EX-001"])
    draft["cases"].append(_case("EX-001"))
    draft_path = tmp_path / "draft.yaml"
    draft_path.write_text(yaml.safe_dump(draft, sort_keys=False), encoding="utf-8")

    with pytest.raises(PydanticValidationError):
        validate_draft(draft_path)


def test_validate_draft_rejects_confidence_out_of_range(tmp_path: Path) -> None:
    draft = _draft(["EX-001"])
    draft["cases"][0]["judge_confidence"] = 1.5
    draft_path = tmp_path / "draft.yaml"
    draft_path.write_text(yaml.safe_dump(draft, sort_keys=False), encoding="utf-8")

    with pytest.raises(PydanticValidationError):
        validate_draft(draft_path)


def test_shipped_example_draft_validates() -> None:
    summary = validate_draft(ROOT / "evaluation/golden/drafts/example-draft.yaml")

    assert summary["case_count"] == 1
    assert summary["draft"] == "example-draft"


# --- draft review --------------------------------------------------------


def test_record_draft_review_decision_creates_and_updates(tmp_path: Path) -> None:
    draft_path = _write_draft(tmp_path / "draft.yaml", ["EX-001", "EX-002"])
    review_path = tmp_path / "review.yaml"

    result = record_draft_review_decision(
        draft_path=draft_path,
        review_path=review_path,
        case_id="EX-001",
        action="approve",
        reviewer="reviewer-a",
    )
    assert result["decision_count"] == 1
    review = load_draft_review(review_path)
    assert review.decisions[0].action == "approve"

    # re-recording the same case id updates the decision instead of duplicating it
    record_draft_review_decision(
        draft_path=draft_path,
        review_path=review_path,
        case_id="EX-001",
        action="reject",
        reviewer="reviewer-a",
        note="changed my mind",
    )
    review = load_draft_review(review_path)
    assert len(review.decisions) == 1
    assert review.decisions[0].action == "reject"
    assert review.decisions[0].note == "changed my mind"


def test_record_draft_review_decision_refuses_unknown_case_id(tmp_path: Path) -> None:
    draft_path = _write_draft(tmp_path / "draft.yaml", ["EX-001"])
    review_path = tmp_path / "review.yaml"

    with pytest.raises(ValidationError):
        record_draft_review_decision(
            draft_path=draft_path,
            review_path=review_path,
            case_id="EX-999",
            action="approve",
            reviewer="reviewer-a",
        )


def test_record_draft_review_decision_refuses_fingerprint_mismatch(tmp_path: Path) -> None:
    draft_path = _write_draft(tmp_path / "draft.yaml", ["EX-001", "EX-002"])
    review_path = tmp_path / "review.yaml"
    record_draft_review_decision(
        draft_path=draft_path,
        review_path=review_path,
        case_id="EX-001",
        action="approve",
        reviewer="reviewer-a",
    )

    # draft content changes after the review was recorded
    _write_draft(draft_path, ["EX-001", "EX-002", "EX-003"])

    with pytest.raises(ValidationError):
        record_draft_review_decision(
            draft_path=draft_path,
            review_path=review_path,
            case_id="EX-002",
            action="approve",
            reviewer="reviewer-a",
        )


# --- draft promote ---------------------------------------------------------


def _promote_ready_draft_and_review(
    tmp_path: Path,
    *,
    case_ids: list[str],
    reviewed_case_ids: list[str],
    rejected_case_ids: list[str] | None = None,
    reviewer: str = "reviewer-a",
    name: str = "draft-fixture",
) -> tuple[Path, Path]:
    rejected = set(rejected_case_ids or [])
    draft_path = _write_draft(tmp_path / "draft.yaml", case_ids, name=name)
    fingerprint = compute_draft_fingerprint(draft_path.read_bytes())
    decisions = [
        {"case_id": case_id, "action": "reject" if case_id in rejected else "approve"}
        for case_id in reviewed_case_ids
    ]
    review_path = _write_review_file(
        tmp_path / "review.yaml",
        draft_name=name,
        draft_fingerprint=fingerprint,
        reviewer=reviewer,
        decisions=decisions,
    )
    return draft_path, review_path


def test_promote_draft_happy_path_into_fresh_dataset(tmp_path: Path) -> None:
    case_ids = ["EX-001", "EX-002", "EX-003", "EX-004", "EX-005"]
    draft_path, review_path = _promote_ready_draft_and_review(
        tmp_path, case_ids=case_ids, reviewed_case_ids=["EX-001"]
    )
    dataset_path = tmp_path / "dataset.yaml"

    summary = promote_draft(
        draft_path=draft_path,
        review_path=review_path,
        dataset_path=dataset_path,
        min_sample_rate=0.2,
        dataset_version="1.0.0",
    )

    assert summary["promoted_case_count"] == 5
    assert summary["sample_rate"] == pytest.approx(0.2)
    assert summary["reviewer"] == "reviewer-a"
    assert summary["lifecycle"] == "reviewed"
    assert summary["dataset_version"] == "1.0.0"
    assert summary["source_revision"] == "sha256:fixture"

    # the real contract: the promoted file loads through the existing loader
    loaded = load_dataset(dataset_path)
    assert loaded.name == "draft-fixture"
    assert {case.id for case in loaded.cases} == set(case_ids)
    for case in loaded.cases:
        assert case.notes is not None
        assert "judge-proposed (generation_model)" in case.notes
        assert "sample-audited by reviewer-a" in case.notes
        assert case.lifecycle == "reviewed"
        assert case.version == "1.0.0"
        assert case.reviewer == "reviewer-a"
        assert case.source_revision == "sha256:fixture"

    # a freshly created dataset also carries dataset-level canonical authority,
    # so the promoted file satisfies the gate the runner checks before promoting
    # a variant to the leaderboard.
    assert loaded.gate_eligible is True


def test_promote_draft_happy_path_into_existing_dataset(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.yaml"
    dataset_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "kip.golden-dataset.v1",
                "name": "existing",
                "corpus_fingerprint": "sha256:existing",
                "cases": [
                    {
                        "id": "PRE-001",
                        "question": "기존 질문",
                        "category": "semantic_paraphrase",
                        "principal": "principal_public",
                        "acl_scopes": ["workspace:default"],
                        "expected_documents": ["ldoc_pre_001"],
                        "forbidden_documents": [],
                        "recall_at": 10,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    case_ids = ["EX-001", "EX-002"]
    draft_path, review_path = _promote_ready_draft_and_review(
        tmp_path, case_ids=case_ids, reviewed_case_ids=["EX-001", "EX-002"]
    )

    summary = promote_draft(
        draft_path=draft_path,
        review_path=review_path,
        dataset_path=dataset_path,
        min_sample_rate=0.2,
        dataset_version="1.0.0",
    )

    assert summary["promoted_case_count"] == 2
    assert summary["total_dataset_case_count"] == 3

    loaded = load_dataset(dataset_path)
    assert {case.id for case in loaded.cases} == {"PRE-001", "EX-001", "EX-002"}
    pre_existing = next(case for case in loaded.cases if case.id == "PRE-001")
    assert pre_existing.notes is None
    # promotion into an existing dataset does not rewrite the pre-existing
    # dataset-level authority fields, only the newly appended cases.
    assert loaded.version == "draft"
    for case_id in ("EX-001", "EX-002"):
        promoted_case = next(case for case in loaded.cases if case.id == case_id)
        assert promoted_case.version == "1.0.0"
        assert promoted_case.reviewer == "reviewer-a"
        assert promoted_case.source_revision == "sha256:fixture"


def test_promote_draft_fails_on_low_sample_rate(tmp_path: Path) -> None:
    case_ids = ["EX-001", "EX-002", "EX-003", "EX-004", "EX-005"]
    draft_path, review_path = _promote_ready_draft_and_review(
        tmp_path, case_ids=case_ids, reviewed_case_ids=[]
    )
    dataset_path = tmp_path / "dataset.yaml"

    with pytest.raises(ValidationError):
        promote_draft(
            draft_path=draft_path,
            review_path=review_path,
            dataset_path=dataset_path,
            min_sample_rate=0.2,
        )
    assert not dataset_path.exists()


def test_promote_draft_fails_on_rejected_sampled_case(tmp_path: Path) -> None:
    case_ids = ["EX-001", "EX-002", "EX-003", "EX-004", "EX-005"]
    draft_path, review_path = _promote_ready_draft_and_review(
        tmp_path,
        case_ids=case_ids,
        reviewed_case_ids=["EX-001", "EX-002"],
        rejected_case_ids=["EX-002"],
    )
    dataset_path = tmp_path / "dataset.yaml"

    with pytest.raises(ValidationError):
        promote_draft(
            draft_path=draft_path,
            review_path=review_path,
            dataset_path=dataset_path,
            min_sample_rate=0.2,
        )
    assert not dataset_path.exists()


def test_promote_draft_fails_on_id_collision(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.yaml"
    dataset_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "kip.golden-dataset.v1",
                "name": "existing",
                "cases": [
                    {
                        "id": "EX-001",
                        "question": "기존 질문",
                        "category": "semantic_paraphrase",
                        "principal": "principal_public",
                        "acl_scopes": ["workspace:default"],
                        "expected_documents": ["ldoc_ex_001"],
                        "forbidden_documents": [],
                        "recall_at": 10,
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    case_ids = ["EX-001", "EX-002"]
    draft_path, review_path = _promote_ready_draft_and_review(
        tmp_path, case_ids=case_ids, reviewed_case_ids=["EX-001", "EX-002"]
    )
    original_bytes = dataset_path.read_bytes()

    with pytest.raises(ValidationError):
        promote_draft(
            draft_path=draft_path,
            review_path=review_path,
            dataset_path=dataset_path,
            min_sample_rate=0.2,
        )
    assert dataset_path.read_bytes() == original_bytes


def test_promote_draft_refuses_fingerprint_mismatch(tmp_path: Path) -> None:
    case_ids = ["EX-001", "EX-002"]
    draft_path, review_path = _promote_ready_draft_and_review(
        tmp_path, case_ids=case_ids, reviewed_case_ids=["EX-001", "EX-002"]
    )
    _write_draft(draft_path, ["EX-001", "EX-002", "EX-003"])
    dataset_path = tmp_path / "dataset.yaml"

    with pytest.raises(ValidationError):
        promote_draft(
            draft_path=draft_path,
            review_path=review_path,
            dataset_path=dataset_path,
            min_sample_rate=0.2,
        )


# --- canonical-authority forgery protection --------------------------------


def test_draft_case_refuses_judge_authored_reviewer_field(tmp_path: Path) -> None:
    draft = _draft(["EX-001"])
    draft["cases"][0]["reviewer"] = "self-declared-reviewer"
    draft_path = tmp_path / "draft.yaml"
    draft_path.write_text(yaml.safe_dump(draft, sort_keys=False), encoding="utf-8")

    with pytest.raises(PydanticValidationError, match="canonical-authority"):
        validate_draft(draft_path)


def test_draft_case_refuses_judge_authored_lifecycle_field(tmp_path: Path) -> None:
    draft = _draft(["EX-001"])
    draft["cases"][0]["lifecycle"] = "golden"
    draft_path = tmp_path / "draft.yaml"
    draft_path.write_text(yaml.safe_dump(draft, sort_keys=False), encoding="utf-8")

    with pytest.raises(PydanticValidationError, match="canonical-authority"):
        validate_draft(draft_path)


def test_promote_draft_refuses_judge_authored_authority_field(tmp_path: Path) -> None:
    draft = _draft(["EX-001", "EX-002"])
    draft["cases"][0]["version"] = "1.0.0"
    draft_path = tmp_path / "draft.yaml"
    draft_path.write_text(yaml.safe_dump(draft, sort_keys=False), encoding="utf-8")
    fingerprint = compute_draft_fingerprint(draft_path.read_bytes())
    review_path = _write_review_file(
        tmp_path / "review.yaml",
        draft_name="draft-fixture",
        draft_fingerprint=fingerprint,
        reviewer="reviewer-a",
        decisions=[{"case_id": "EX-001", "action": "approve"}],
    )
    dataset_path = tmp_path / "dataset.yaml"

    with pytest.raises(PydanticValidationError, match="canonical-authority"):
        promote_draft(
            draft_path=draft_path,
            review_path=review_path,
            dataset_path=dataset_path,
            min_sample_rate=0.2,
            dataset_version="1.0.0",
        )
    assert not dataset_path.exists()


# --- draft promote: explicit authority assignment --------------------------


def test_promote_draft_refuses_missing_source_revision_without_corpus_fingerprint(
    tmp_path: Path,
) -> None:
    draft = _draft(["EX-001"])
    del draft["corpus_fingerprint"]
    draft_path = tmp_path / "draft.yaml"
    draft_path.write_text(yaml.safe_dump(draft, sort_keys=False), encoding="utf-8")
    fingerprint = compute_draft_fingerprint(draft_path.read_bytes())
    review_path = _write_review_file(
        tmp_path / "review.yaml",
        draft_name="draft-fixture",
        draft_fingerprint=fingerprint,
        reviewer="reviewer-a",
        decisions=[{"case_id": "EX-001", "action": "approve"}],
    )
    dataset_path = tmp_path / "dataset.yaml"

    with pytest.raises(ValidationError, match="source-revision"):
        promote_draft(
            draft_path=draft_path,
            review_path=review_path,
            dataset_path=dataset_path,
            min_sample_rate=0.2,
            dataset_version="1.0.0",
        )
    assert not dataset_path.exists()


def test_promote_draft_refuses_missing_dataset_version_for_fresh_dataset(tmp_path: Path) -> None:
    case_ids = ["EX-001", "EX-002"]
    draft_path, review_path = _promote_ready_draft_and_review(
        tmp_path, case_ids=case_ids, reviewed_case_ids=["EX-001", "EX-002"]
    )
    dataset_path = tmp_path / "dataset.yaml"

    with pytest.raises(ValidationError, match="dataset-version"):
        promote_draft(
            draft_path=draft_path,
            review_path=review_path,
            dataset_path=dataset_path,
            min_sample_rate=0.2,
        )
    assert not dataset_path.exists()


def test_promote_draft_refuses_draft_lifecycle_option(tmp_path: Path) -> None:
    case_ids = ["EX-001", "EX-002"]
    draft_path, review_path = _promote_ready_draft_and_review(
        tmp_path, case_ids=case_ids, reviewed_case_ids=["EX-001", "EX-002"]
    )
    dataset_path = tmp_path / "dataset.yaml"

    with pytest.raises(ValidationError, match="lifecycle"):
        promote_draft(
            draft_path=draft_path,
            review_path=review_path,
            dataset_path=dataset_path,
            min_sample_rate=0.2,
            lifecycle="draft",
            dataset_version="1.0.0",
        )
    assert not dataset_path.exists()


def test_promote_draft_refuses_draft_dataset_version_option(tmp_path: Path) -> None:
    case_ids = ["EX-001", "EX-002"]
    draft_path, review_path = _promote_ready_draft_and_review(
        tmp_path, case_ids=case_ids, reviewed_case_ids=["EX-001", "EX-002"]
    )
    dataset_path = tmp_path / "dataset.yaml"

    with pytest.raises(ValidationError, match="dataset-version"):
        promote_draft(
            draft_path=draft_path,
            review_path=review_path,
            dataset_path=dataset_path,
            min_sample_rate=0.2,
            dataset_version="draft",
        )
    assert not dataset_path.exists()


def test_load_draft_helper_round_trips(tmp_path: Path) -> None:
    draft_path = _write_draft(tmp_path / "draft.yaml", ["EX-001"])

    draft = load_draft(draft_path)

    assert draft.name == "draft-fixture"
    assert draft.cases[0].judge_confidence == 0.8


# --- CLI end-to-end --------------------------------------------------------


def test_cli_draft_validate_review_promote_round_trip(tmp_path: Path) -> None:
    draft_path = _write_draft(tmp_path / "draft.yaml", ["EX-001", "EX-002"])
    review_path = tmp_path / "review.yaml"
    dataset_path = tmp_path / "dataset.yaml"
    runner = CliRunner()

    validated = runner.invoke(
        app,
        ["evaluate", "draft", "validate", "--draft", str(draft_path)],
        env=_env(),
    )
    assert validated.exit_code == 0, validated.stdout
    assert '"case_count": 2' in validated.stdout

    for case_id in ("EX-001", "EX-002"):
        reviewed = runner.invoke(
            app,
            [
                "evaluate",
                "draft",
                "review",
                "--draft",
                str(draft_path),
                "--review",
                str(review_path),
                "--case-id",
                case_id,
                "--action",
                "approve",
                "--reviewer",
                "reviewer-a",
            ],
            env=_env(),
        )
        assert reviewed.exit_code == 0, reviewed.stdout

    promoted = runner.invoke(
        app,
        [
            "evaluate",
            "draft",
            "promote",
            "--draft",
            str(draft_path),
            "--review",
            str(review_path),
            "--dataset",
            str(dataset_path),
            "--min-sample-rate",
            "0.2",
            "--dataset-version",
            "1.0.0",
        ],
        env=_env(),
    )
    assert promoted.exit_code == 0, promoted.stdout
    assert '"promoted_case_count": 2' in promoted.stdout

    loaded = load_dataset(dataset_path)
    assert {case.id for case in loaded.cases} == {"EX-001", "EX-002"}
