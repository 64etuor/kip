from __future__ import annotations

from pathlib import Path

import pytest

from kip.errors import ValidationError
from kip.setup.service import SetupService
from tests.setup_support import complete_setup_answers


def test_setup_state_save_and_resume_is_atomic(tmp_path: Path) -> None:
    state = tmp_path / "state/setup.json"
    service = SetupService(project_root=tmp_path / "project", state_path=state)

    service.record_answer("workspace", "acme-rnd")
    restored = SetupService(
        project_root=tmp_path / "project",
        state_path=state,
    ).load_answers()

    assert restored.workspace == "acme-rnd"
    assert not list(state.parent.glob("*.tmp"))


def test_setup_state_revalidates_tampered_source_roots(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    answers = complete_setup_answers(tmp_path).model_copy(
        update={
            "filesystem_sources": [
                complete_setup_answers(tmp_path).filesystem_sources[0].model_copy(
                    update={"root": "/"}
                )
            ]
        }
    )
    state.write_text(answers.model_dump_json(), encoding="utf-8")

    with pytest.raises(ValidationError, match="too broad"):
        SetupService(
            project_root=tmp_path / "project",
            state_path=state,
        ).load_answers()


def test_managed_storage_cannot_overlap_read_only_source(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = tmp_path / "state.json"
    answers = complete_setup_answers(tmp_path).model_copy(update={"cas_path": None})
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    source = answers.filesystem_sources[0]
    service = SetupService(project_root=project_root, state_path=state)

    with pytest.raises(ValidationError, match="overlap"):
        service.record_answer("cas_path", str(Path(source.root) / "cas"))

    assert not (Path(source.root) / "cas").exists()
