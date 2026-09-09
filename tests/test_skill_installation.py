from __future__ import annotations

from pathlib import Path

import pytest

from scripts.install_agent_files import install_skills


def _trees(tmp_path: Path) -> tuple[Path, Path, Path]:
    source, destination, pointer = tmp_path / "source", tmp_path / "target", tmp_path / "config/root"
    for name in ("knowledge-fabric", "kip-setup"):
        (source / name).mkdir(parents=True)
        (source / name / "SKILL.md").write_text(f"new {name}")
        (destination / name).mkdir(parents=True)
        (destination / name / "SKILL.md").write_text(f"old {name}")
    pointer.parent.mkdir()
    pointer.write_text("previous-runtime\n")
    return source, destination, pointer


def test_install_replaces_bundles_and_updates_pointer(tmp_path: Path) -> None:
    source, destination, pointer = _trees(tmp_path)
    (destination / "unrelated").mkdir()
    (destination / "knowledge-fabric/obsolete.md").write_text("old")
    install_skills(source, destination, pointer, tmp_path)
    assert (destination / "knowledge-fabric/SKILL.md").read_text() == "new knowledge-fabric"
    assert not (destination / "knowledge-fabric/obsolete.md").exists()
    assert (destination / "unrelated").is_dir()
    assert pointer.read_text() == f"{tmp_path}\n"


def test_missing_source_preserves_both_installed_skills(tmp_path: Path) -> None:
    source, destination, pointer = _trees(tmp_path)
    (source / "kip-setup/SKILL.md").unlink()
    with pytest.raises(ValueError, match=r"SKILL\.md"):
        install_skills(source, destination, pointer, tmp_path)
    assert (destination / "knowledge-fabric/SKILL.md").read_text() == "old knowledge-fabric"
    assert pointer.read_text() == "previous-runtime\n"


def test_pointer_failure_rolls_back_both_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, destination, pointer = _trees(tmp_path)
    original = Path.replace

    def fail_pointer(self: Path, target: Path) -> Path:
        if target == pointer:
            raise OSError("simulated pointer failure")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", fail_pointer)
    with pytest.raises(OSError, match="pointer failure"):
        install_skills(source, destination, pointer, tmp_path)
    for name in ("knowledge-fabric", "kip-setup"):
        assert (destination / name / "SKILL.md").read_text() == f"old {name}"
    assert pointer.read_text() == "previous-runtime\n"


def test_symlink_destination_is_rejected_without_touching_target(tmp_path: Path) -> None:
    source, destination, pointer = _trees(tmp_path)
    external = tmp_path / "external"
    (destination / "kip-setup").rename(external)
    (destination / "kip-setup").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        install_skills(source, destination, pointer, tmp_path)
    assert (external / "SKILL.md").read_text() == "old kip-setup"
    assert (destination / "knowledge-fabric/SKILL.md").read_text() == "old knowledge-fabric"


def test_second_replacement_failure_restores_first_and_second_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, pointer = _trees(tmp_path)
    original = Path.replace

    def fail_second(self: Path, target: Path) -> Path:
        if self.name == "kip-setup" and target == destination / "kip-setup":
            raise OSError("simulated replacement failure")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", fail_second)
    with pytest.raises(OSError, match="replacement failure"):
        install_skills(source, destination, pointer, tmp_path)
    for name in ("knowledge-fabric", "kip-setup"):
        assert (destination / name / "SKILL.md").read_text() == f"old {name}"
    assert pointer.read_text() == "previous-runtime\n"
