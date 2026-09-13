from __future__ import annotations

import json
from pathlib import Path

import pytest

from kip.skill_installs import (
    CURRENT,
    MISSING,
    OTHER_DEPLOYMENT,
    RECORD_FILENAME,
    REGISTRY_PATH,
    STALE,
    UNRECORDED,
    InstallRecord,
    RegisteredLocation,
    find_recorded_installs,
    format_install_record,
    format_registry,
    is_within,
    parse_install_record,
    read_install_record,
    same_path,
    skill_install_statuses,
)


def _record(skill: str, deployment: Path, version: str) -> str:
    return format_install_record(
        InstallRecord(skill, deployment, version, "claude", "project", "2026-09-13T00:00:00Z")
    )


def test_record_round_trips_and_rejects_incomplete_or_relative_records(tmp_path: Path) -> None:
    record = InstallRecord("kip-setup", tmp_path / "my deployment=1", "3.15.0", "codex", "personal", "2026-09-13T00:00:00Z")
    assert parse_install_record(format_install_record(record)) == record
    assert parse_install_record("schema=kip.skill-install.v1\nskill=kip-setup\n") is None
    assert parse_install_record(format_install_record(record).replace(str(tmp_path), "relative")) is None


def test_symlinked_skill_directory_has_no_record(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / RECORD_FILENAME).write_text(_record("kip-setup", tmp_path, "1.0.0"))
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    assert read_install_record(real) is not None
    assert read_install_record(tmp_path / "link") is None


def test_statuses_classify_each_skill_at_each_registered_location(tmp_path: Path) -> None:
    deployment, other = tmp_path / "deployment", tmp_path / "other"
    deployment.mkdir()
    (deployment / "VERSION").write_text("2.0.0\n")
    fresh, mixed = tmp_path / "fresh/.claude/skills", tmp_path / "mixed/.agents/skills"
    for skill in ("knowledge-fabric", "kip-setup"):
        (fresh / skill).mkdir(parents=True)
        (fresh / skill / RECORD_FILENAME).write_text(_record(skill, deployment, "2.0.0"))
    (mixed / "knowledge-fabric").mkdir(parents=True)
    (mixed / "knowledge-fabric" / RECORD_FILENAME).write_text(_record("knowledge-fabric", deployment, "1.0.0"))
    (deployment / REGISTRY_PATH).parent.mkdir()
    (deployment / REGISTRY_PATH).write_text(format_registry([
        RegisteredLocation(fresh, "claude", "project"),
        RegisteredLocation(mixed, "codex", "project"),
    ]))

    states = {(status.destination, status.skill): status for status in skill_install_statuses(deployment)}
    assert {key: status.state for key, status in states.items()} == {
        (fresh, "knowledge-fabric"): CURRENT, (fresh, "kip-setup"): CURRENT,
        (mixed, "knowledge-fabric"): STALE, (mixed, "kip-setup"): MISSING,
    }
    assert states[(mixed, "knowledge-fabric")].installed_version == "1.0.0"
    assert states[(mixed, "knowledge-fabric")].deployment_version == "2.0.0"
    assert not states[(mixed, "knowledge-fabric")].ok and states[(fresh, "kip-setup")].ok

    (mixed / "kip-setup").mkdir()
    (mixed / "knowledge-fabric" / RECORD_FILENAME).write_text(_record("knowledge-fabric", other, "2.0.0"))
    states = {(status.destination, status.skill): status for status in skill_install_statuses(deployment)}
    assert states[(mixed, "kip-setup")].state == UNRECORDED
    assert states[(mixed, "knowledge-fabric")].state == OTHER_DEPLOYMENT
    assert states[(mixed, "knowledge-fabric")].recorded_deployment == other


def test_absent_registry_is_empty_and_malformed_registry_is_an_error(tmp_path: Path) -> None:
    assert find_recorded_installs(tmp_path) == ()
    assert skill_install_statuses(tmp_path) == ()
    (tmp_path / REGISTRY_PATH).parent.mkdir()
    (tmp_path / REGISTRY_PATH).write_text("{not json")
    with pytest.raises(ValueError, match="unreadable"):
        find_recorded_installs(tmp_path)
    (tmp_path / REGISTRY_PATH).write_text(json.dumps({"schema": "kip.skill-install-registry.v1", "installs": [{"client": "claude"}]}))
    with pytest.raises(ValueError, match="malformed"):
        find_recorded_installs(tmp_path)


@pytest.mark.parametrize(
    "payload",
    ["[]", "null", '"x"', json.dumps({"schema": "kip.skill-install-registry.v1", "installs": [{"destination": "relative/.claude/skills", "client": "claude", "scope": "project"}]})],
)
def test_a_registry_that_is_not_a_v1_object_is_a_value_error(tmp_path: Path, payload: str) -> None:
    (tmp_path / REGISTRY_PATH).parent.mkdir()
    (tmp_path / REGISTRY_PATH).write_text(payload)
    with pytest.raises(ValueError, match="registry"):
        find_recorded_installs(tmp_path)
    with pytest.raises(ValueError, match="registry"):
        skill_install_statuses(tmp_path)


def test_same_path_and_is_within_compare_by_identity(tmp_path: Path) -> None:
    root = tmp_path / "deployment"
    (root / ".claude/skills").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    assert same_path(link, root)
    assert is_within(link / ".claude/skills", root)
    assert is_within(link / "not/created/yet", root)
    assert not is_within(tmp_path / "elsewhere/.claude/skills", root)
    cased = tmp_path / "DEPLOYMENT"
    if cased.exists():  # case-insensitive filesystem
        assert same_path(cased, root) and is_within(cased / ".claude/skills", root)
