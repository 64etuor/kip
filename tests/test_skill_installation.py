from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kip.skill_installs import (
    RECORD_FILENAME,
    REGISTRY_PATH,
    find_recorded_installs,
    read_install_record,
)
from scripts.install_agent_files import install_skills

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("knowledge-fabric", "kip-setup")


def _trees(tmp_path: Path) -> tuple[Path, Path, Path]:
    source, destination, deployment = tmp_path / "source", tmp_path / "target", tmp_path / "deployment"
    for name in SKILLS:
        (source / name).mkdir(parents=True)
        (source / name / "SKILL.md").write_text(f"new {name}")
        (destination / name).mkdir(parents=True)
        (destination / name / "SKILL.md").write_text(f"old {name}")
    deployment.mkdir()
    (deployment / "VERSION").write_text("3.15.0\n")
    return source, destination, deployment


def test_install_replaces_bundles_records_the_deployment_and_registers_the_location(tmp_path: Path) -> None:
    source, destination, deployment = _trees(tmp_path)
    (destination / "unrelated").mkdir()
    (destination / "knowledge-fabric/obsolete.md").write_text("old")
    install_skills(source, destination, deployment, "claude", "project")
    assert (destination / "knowledge-fabric/SKILL.md").read_text() == "new knowledge-fabric"
    assert not (destination / "knowledge-fabric/obsolete.md").exists()
    assert (destination / "unrelated").is_dir()
    for name in SKILLS:
        record = read_install_record(destination / name)
        assert record is not None
        assert (record.skill, record.deployment, record.version) == (name, deployment.resolve(), "3.15.0")
    assert [item.destination for item in find_recorded_installs(deployment)] == [destination.resolve()]
    assert not (source / "knowledge-fabric" / RECORD_FILENAME).exists()


def test_missing_source_preserves_both_installed_skills(tmp_path: Path) -> None:
    source, destination, deployment = _trees(tmp_path)
    (source / "kip-setup/SKILL.md").unlink()
    with pytest.raises(ValueError, match=r"SKILL\.md"):
        install_skills(source, destination, deployment, "claude", "project")
    assert (destination / "knowledge-fabric/SKILL.md").read_text() == "old knowledge-fabric"
    assert not (deployment / REGISTRY_PATH).exists()


def test_registry_failure_rolls_back_both_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, destination, deployment = _trees(tmp_path)
    registry = deployment / REGISTRY_PATH
    original = Path.replace

    def fail_registry(self: Path, target: Path) -> Path:
        if target == registry:
            raise OSError("simulated registry failure")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", fail_registry)
    with pytest.raises(OSError, match="registry failure"):
        install_skills(source, destination, deployment, "claude", "project")
    for name in SKILLS:
        assert (destination / name / "SKILL.md").read_text() == f"old {name}"
        assert not (destination / name / RECORD_FILENAME).exists()
    assert not registry.exists()


def test_symlink_destination_is_rejected_without_touching_target(tmp_path: Path) -> None:
    source, destination, deployment = _trees(tmp_path)
    external = tmp_path / "external"
    (destination / "kip-setup").rename(external)
    (destination / "kip-setup").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        install_skills(source, destination, deployment, "claude", "project")
    assert (external / "SKILL.md").read_text() == "old kip-setup"
    assert (destination / "knowledge-fabric/SKILL.md").read_text() == "old knowledge-fabric"


def test_second_replacement_failure_restores_first_and_second_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, deployment = _trees(tmp_path)
    original = Path.replace

    def fail_second(self: Path, target: Path) -> Path:
        if self.name == "kip-setup" and target == destination / "kip-setup":
            raise OSError("simulated replacement failure")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", fail_second)
    with pytest.raises(OSError, match="replacement failure"):
        install_skills(source, destination, deployment, "claude", "project")
    for name in SKILLS:
        assert (destination / name / "SKILL.md").read_text() == f"old {name}"
    assert not (deployment / REGISTRY_PATH).exists()


# End to end, through the shipped wrappers, in a throwaway HOME and throwaway
# deployments and projects. Nothing here may touch the real HOME.


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _deployment(root: Path, label: str, version: str) -> Path:
    (root / "scripts").mkdir(parents=True)
    (root / "src/kip").mkdir(parents=True)
    for name in (
        "install_agent_files.py", "install-agent-files.sh", "uninstall-agent-files.sh",
        "upgrade.sh", "common.sh", "runtime-path.sh",
    ):
        shutil.copy2(ROOT / "scripts" / name, root / "scripts" / name)
    shutil.copy2(ROOT / "src/kip/skill_installs.py", root / "src/kip/skill_installs.py")
    (root / "src/kip/__init__.py").write_text('"""Deployment fixture."""\n')
    shutil.copytree(ROOT / "skills", root / "skills", ignore=shutil.ignore_patterns(".DS_Store"))
    (root / "VERSION").write_text(f"{version}\n")
    (root / "AGENTS.md").write_text("# KIP\n")
    _executable(root / "scripts/kip", f"#!/bin/bash\nprintf '%s\\n' '{label}' \"$@\"\n")
    _executable(root / "scripts/bootstrap.sh", "#!/bin/bash\necho bootstrap-stub\n")
    _executable(root / "scripts/migrate.sh", "#!/bin/bash\necho migrate-stub\n")
    return root


def _env(home: Path) -> dict[str, str]:
    # Explicit: no CLAUDE_PROJECT_DIR or KIP_PROJECT_DIR from the launching shell.
    return {
        "HOME": str(home),
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "KIP_SKIP_DOTENV": "1",
    }


def _run(command: list[str], home: Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=_env(home), capture_output=True, text=True, check=False)


def _install(deployment: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = _run(["/bin/bash", str(deployment / "scripts/install-agent-files.sh"), *args], home)
    assert result.returncode == 0, result.stderr + result.stdout
    return result


def _resolve(skills: Path, home: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return _run(["/bin/bash", str(skills / "knowledge-fabric/scripts/kip.sh"), "capabilities"], home, cwd)


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Path]:
    places = {name: tmp_path / name for name in ("home", "project-a", "project-b", "unrelated")}
    for place in places.values():
        place.mkdir()
    places["x"] = _deployment(tmp_path / "deployment-x", "deployment-x", "1.0.0")
    places["y"] = _deployment(tmp_path / "deployment-y", "deployment-y", "2.0.0")
    return places


def test_two_deployments_installed_into_two_projects_keep_resolving_to_their_own(world: dict[str, Path]) -> None:
    home = world["home"]
    _install(world["x"], home, "project", str(world["project-a"]), "--client", "all")
    _install(world["y"], home, "project", str(world["project-b"]))

    for cwd in (world["project-a"], world["project-b"], world["unrelated"]):
        for skills in (world["project-a"] / ".claude/skills", world["project-a"] / ".agents/skills"):
            result = _resolve(skills, home, cwd)
            assert result.stdout.splitlines() == ["deployment-x", "capabilities"], result.stderr
        result = _resolve(world["project-b"] / ".claude/skills", home, cwd)
        assert result.stdout.splitlines() == ["deployment-y", "capabilities"], result.stderr
    assert not (home / ".config/kip/project-root").exists()
    assert not (world["project-b"] / ".agents").exists()


def test_personal_install_resolves_from_an_unrelated_directory(world: dict[str, Path]) -> None:
    home = world["home"]
    output = _install(world["x"], home, "personal", "--client", "all").stdout
    assert "for codex at" in output and "for claude at" in output
    for skills in (home / ".claude/skills", home / ".agents/skills"):
        result = _resolve(skills, home, world["unrelated"])
        assert result.stdout.splitlines() == ["deployment-x", "capabilities"], result.stderr


def test_a_recorded_deployment_that_is_gone_stops_and_only_unrecorded_copies_use_the_legacy_pointer(
    world: dict[str, Path],
) -> None:
    home = world["home"]
    skills = home / ".claude/skills"
    _install(world["x"], home, "personal")
    (home / ".config/kip").mkdir(parents=True)
    (home / ".config/kip/project-root").write_text(f"{world['y']}\n")
    (world["x"] / "scripts/kip").unlink()

    stopped = _resolve(skills, home, world["unrelated"])
    assert stopped.returncode == 2 and not stopped.stdout
    assert f"installed from {world['x'].resolve()}" in stopped.stderr

    (skills / "knowledge-fabric" / RECORD_FILENAME).unlink()  # a copy from before install records
    legacy = _resolve(skills, home, world["unrelated"])
    assert legacy.stdout.splitlines() == ["deployment-y", "capabilities"], legacy.stderr


def test_a_project_install_into_the_deployment_itself_is_refused(world: dict[str, Path]) -> None:
    result = _run(["/bin/bash", str(world["x"] / "scripts/install-agent-files.sh"), "project", str(world["x"])], world["home"])
    assert result.returncode == 1
    assert "deployment itself" in result.stderr
    assert not (world["x"] / ".claude").exists()
    assert not (world["x"] / REGISTRY_PATH).exists()


def _finish_upgrade(deployment: Path, home: Path) -> subprocess.CompletedProcess[str]:
    return _run(["/bin/bash", str(deployment / "scripts/upgrade.sh"), "--finish"], home)


def test_upgrade_refreshes_recorded_installs_and_skips_removed_or_foreign_locations(
    world: dict[str, Path], tmp_path: Path,
) -> None:
    home, x = world["home"], world["x"]
    stale, removed, taken = world["project-a"], world["project-b"], tmp_path / "project-c"
    taken.mkdir()
    for project in (stale, removed, taken):
        _install(x, home, "project", str(project))
    (stale / ".claude/skills/knowledge-fabric/SKILL.md").write_text("stale text")
    shutil.rmtree(removed / ".claude")
    _install(world["y"], home, "project", str(taken))  # now belongs to another deployment
    (x / "VERSION").write_text("1.1.0\n")

    result = _finish_upgrade(x, home)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Upgrade complete" in result.stdout
    assert f"Refreshed {(stale / '.claude/skills').resolve()} (1.0.0 -> 1.1.0)" in result.stdout
    assert f"Skipped {(removed / '.claude/skills').resolve()}: the location was removed" in result.stdout
    assert f"now belongs to deployment {world['y'].resolve()}" in result.stdout
    for name in SKILLS:
        record = read_install_record(stale / ".claude/skills" / name)
        assert record is not None and record.version == "1.1.0"
    assert (stale / ".claude/skills/knowledge-fabric/SKILL.md").read_bytes() == (ROOT / "skills/knowledge-fabric/SKILL.md").read_bytes()
    assert not (removed / ".claude").exists()
    taken_record = read_install_record(taken / ".claude/skills/kip-setup")
    assert taken_record is not None and taken_record.deployment == world["y"].resolve()


def test_a_failed_skill_refresh_is_reported_and_does_not_fail_the_upgrade(world: dict[str, Path]) -> None:
    home, x, project = world["home"], world["x"], world["project-a"]
    _install(x, home, "project", str(project))
    (x / "VERSION").write_text("1.1.0\n")
    locked = project / ".claude/skills"
    locked.chmod(0o555)
    try:
        result = _finish_upgrade(x, home)
    finally:
        locked.chmod(0o755)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Upgrade complete" in result.stdout
    assert "Failed to refresh" in result.stderr and "were not all refreshed" in result.stderr
    record = read_install_record(locked / "knowledge-fabric")
    assert record is not None and record.version == "1.0.0"


def test_uninstall_removes_only_skills_installed_by_this_deployment(world: dict[str, Path]) -> None:
    home, x, y, project = world["home"], world["x"], world["y"], world["project-a"]
    _install(x, home, "project", str(project))
    _install(y, home, "project", str(project), "--client", "codex")
    foreign = project / ".agents/skills/kip-setup"
    shutil.rmtree(foreign)
    foreign.mkdir()
    (foreign / "SKILL.md").write_text("someone else's kip-setup")
    (home / ".config/kip").mkdir(parents=True)
    (home / ".config/kip/project-root").write_text(f"{x}\n")

    result = _run(["/bin/bash", str(x / "scripts/uninstall-agent-files.sh"), "project", str(project)], home)

    assert result.returncode == 0, result.stderr + result.stdout
    assert not (project / ".claude/skills/knowledge-fabric").exists()
    assert not (project / ".claude/skills/kip-setup").exists()
    assert (foreign / "SKILL.md").read_text() == "someone else's kip-setup"
    assert read_install_record(project / ".agents/skills/knowledge-fabric") is not None
    assert f"Removed {(project / '.claude/skills').resolve() / 'kip-setup'}" in result.stdout
    assert "kip-setup: no KIP install record" in result.stdout
    assert f"installed from deployment {y.resolve()}" in result.stdout
    assert find_recorded_installs(x) == ()
    assert [item.destination for item in find_recorded_installs(y)] == [(project / ".agents/skills").resolve()]
    assert (home / ".config/kip/project-root").read_text() == f"{x}\n"
    assert "Left the legacy pointer" in result.stdout and "remove it with: rm " in result.stdout


def test_uninstall_leaves_a_legacy_pointer_that_names_another_deployment(world: dict[str, Path]) -> None:
    home = world["home"]
    _install(world["x"], home, "personal")
    (home / ".config/kip").mkdir(parents=True)
    (home / ".config/kip/project-root").write_text(f"{world['y']}\n")

    result = _run(["/bin/bash", str(world["x"] / "scripts/uninstall-agent-files.sh"), "personal"], home)

    assert result.returncode == 0, result.stderr + result.stdout
    assert not (home / ".claude/skills/knowledge-fabric").exists()
    assert (home / ".config/kip/project-root").read_text() == f"{world['y']}\n"
    assert "Left the legacy pointer" in result.stdout
    assert f"Nothing at {home / '.agents/skills'}" in result.stdout


# Legacy adoption: 3.15.0 and earlier installed personal copies without a
# record and pointed ~/.config/kip/project-root at the deployment.


def _legacy_personal_install(home: Path, pointer_target: Path) -> Path:
    skills = home / ".claude/skills"
    for name in SKILLS:
        shutil.copytree(ROOT / "skills" / name, skills / name, ignore=shutil.ignore_patterns(".DS_Store"))
        with (skills / name / "SKILL.md").open("a") as stream:
            stream.write("\nstale 3.15.0 text\n")
    (home / ".config/kip").mkdir(parents=True)
    (home / ".config/kip/project-root").write_text(f"{pointer_target}\n")
    return skills


def test_upgrade_adopts_and_refreshes_a_legacy_personal_install(world: dict[str, Path]) -> None:
    home, x = world["home"], world["x"]
    skills = _legacy_personal_install(home, x)
    (x / "VERSION").write_text("1.1.0\n")

    result = _finish_upgrade(x, home)

    assert result.returncode == 0, result.stderr + result.stdout
    assert f"Adopted {skills}" in result.stdout and "Upgrade complete" in result.stdout
    assert "Refreshed" not in result.stdout
    for name in SKILLS:
        record = read_install_record(skills / name)
        assert record is not None
        assert (record.deployment, record.version, record.scope) == (x.resolve(), "1.1.0", "personal")
        assert (skills / name / "SKILL.md").read_bytes() == (ROOT / "skills" / name / "SKILL.md").read_bytes()
    assert [item.destination for item in find_recorded_installs(x)] == [skills.resolve()]
    assert (home / ".config/kip/project-root").read_text() == f"{x}\n"
    resolved = _resolve(skills, home, world["unrelated"])
    assert resolved.stdout.splitlines() == ["deployment-x", "capabilities"], resolved.stderr


def test_a_legacy_pointer_naming_another_deployment_prevents_adoption(world: dict[str, Path]) -> None:
    home, x = world["home"], world["x"]
    skills = _legacy_personal_install(home, world["y"])

    result = _run(["/bin/bash", str(x / "scripts/install-agent-files.sh"), "--refresh"], home)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Adopted" not in result.stdout and "No recorded skill installs" in result.stdout
    assert read_install_record(skills / "knowledge-fabric") is None
    assert b"stale 3.15.0 text" in (skills / "kip-setup/SKILL.md").read_bytes()
    assert not (x / REGISTRY_PATH).exists()
    assert (home / ".config/kip/project-root").read_text() == f"{world['y']}\n"


def test_a_legacy_project_copy_still_resolves_through_the_pointer_after_adoption(world: dict[str, Path]) -> None:
    home, x, project = world["home"], world["x"], world["project-a"]
    _legacy_personal_install(home, x)
    legacy_project = project / ".claude/skills"
    for name in SKILLS:
        shutil.copytree(ROOT / "skills" / name, legacy_project / name, ignore=shutil.ignore_patterns(".DS_Store"))
    (x / "VERSION").write_text("1.1.0\n")

    result = _finish_upgrade(x, home)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Adopted" in result.stdout
    assert (home / ".config/kip/project-root").read_text() == f"{x}\n"
    assert read_install_record(legacy_project / "knowledge-fabric") is None
    resolved = _resolve(legacy_project, home, world["unrelated"])
    assert resolved.stdout.splitlines() == ["deployment-x", "capabilities"], resolved.stderr

@pytest.mark.parametrize("foreign", ["frontmatter", "symlink"])
def test_a_same_named_skill_that_is_not_kips_is_not_adopted(world: dict[str, Path], tmp_path: Path, foreign: str) -> None:
    home, x = world["home"], world["x"]
    skills = _legacy_personal_install(home, x)
    shutil.rmtree(skills / "kip-setup")
    if foreign == "frontmatter":
        (skills / "kip-setup").mkdir()
        (skills / "kip-setup/SKILL.md").write_text("---\nname: someone-elses-setup\n---\n")
    else:
        elsewhere = tmp_path / "elsewhere"
        shutil.copytree(ROOT / "skills/kip-setup", elsewhere)
        (skills / "kip-setup").symlink_to(elsewhere, target_is_directory=True)
    before = {path: path.read_bytes() for path in sorted(skills.rglob("*")) if path.is_file()}

    result = _run(["/bin/bash", str(x / "scripts/install-agent-files.sh"), "--refresh"], home)

    assert result.returncode == 0, result.stderr + result.stdout
    assert f"Not adopting {skills}" in result.stdout and "Adopted" not in result.stdout
    assert {path: path.read_bytes() for path in sorted(skills.rglob("*")) if path.is_file()} == before
    assert (skills / "kip-setup").is_symlink() == (foreign == "symlink")
    assert not (x / REGISTRY_PATH).exists()
    assert (home / ".config/kip/project-root").read_text() == f"{x}\n"


# Review fixes for 3.15.1.


def _spelling(deployment: Path, tmp_path: Path, spelling: str) -> Path:
    if spelling == "exact":
        return deployment
    if spelling == "symlink":
        link = tmp_path / "deployment-link"
        link.symlink_to(deployment, target_is_directory=True)
        return link
    cased = deployment.with_name(deployment.name.upper())
    if not cased.exists():
        pytest.skip("filesystem is case-sensitive")
    return cased


@pytest.mark.parametrize("spelling", ["symlink", "case"])
def test_install_refuses_the_deployment_under_another_spelling(world: dict[str, Path], tmp_path: Path, spelling: str) -> None:
    x = world["x"]
    target = _spelling(x, tmp_path, spelling)
    result = _run(["/bin/bash", str(x / "scripts/install-agent-files.sh"), "project", str(target), "--client", "all"], world["home"])
    assert result.returncode == 1
    assert result.stderr.count("deployment itself") == 2
    assert not (x / ".claude").exists() and not (x / ".agents").exists()
    assert not (x / REGISTRY_PATH).exists()


def test_refresh_under_the_lock_refuses_a_copy_that_changed_owner(tmp_path: Path) -> None:
    source, destination, deployment = _trees(tmp_path)
    with pytest.raises(ValueError, match="no longer carries"):
        install_skills(source, destination, deployment, "claude", "project", refresh=True)
    other = tmp_path / "other"
    other.mkdir()
    (other / "VERSION").write_text("9.9.9\n")
    install_skills(source, destination, other, "claude", "project")
    with pytest.raises(ValueError, match="now belongs to deployment"):
        install_skills(source, destination, deployment, "claude", "project", refresh=True)
    record = read_install_record(destination / "kip-setup")
    assert record is not None and record.deployment == other.resolve()
    assert not (deployment / REGISTRY_PATH).exists()


def test_an_explicit_install_over_another_deployments_copy_warns(world: dict[str, Path]) -> None:
    home, project = world["home"], world["project-a"]
    _install(world["y"], home, "project", str(project))
    result = _install(world["x"], home, "project", str(project))
    assert f"which was installed from deployment {world['y'].resolve()}" in result.stderr
    record = read_install_record(project / ".claude/skills/kip-setup")
    assert record is not None and record.deployment == world["x"].resolve()


def test_refresh_skips_a_location_missing_one_skill(world: dict[str, Path]) -> None:
    home, x, project = world["home"], world["x"], world["project-a"]
    _install(x, home, "project", str(project))
    shutil.rmtree(project / ".claude/skills/kip-setup")
    (x / "VERSION").write_text("1.1.0\n")

    result = _run(["/bin/bash", str(x / "scripts/install-agent-files.sh"), "--refresh"], home)

    assert result.returncode == 0, result.stderr + result.stdout
    assert f"Skipped {(project / '.claude/skills').resolve()}: kip-setup was removed from it" in result.stdout
    assert not (project / ".claude/skills/kip-setup").exists()
    record = read_install_record(project / ".claude/skills/knowledge-fabric")
    assert record is not None and record.version == "1.0.0"


def test_a_relative_recorded_deployment_stops_instead_of_resolving(world: dict[str, Path], tmp_path: Path) -> None:
    home = world["home"]
    skills = home / ".claude/skills"
    _install(world["x"], home, "personal")
    record = skills / "knowledge-fabric" / RECORD_FILENAME
    record.write_text(record.read_text().replace(f"deployment={world['x'].resolve()}", "deployment=deployment-x"))
    result = _resolve(skills, home, tmp_path)  # tmp_path/deployment-x/scripts/kip exists
    assert result.returncode == 2 and not result.stdout
    assert "installed from deployment-x" in result.stderr


@pytest.mark.parametrize("spelling", ["exact", "symlink", "case"])
def test_uninstall_never_removes_the_deployments_own_skills(world: dict[str, Path], tmp_path: Path, spelling: str) -> None:
    x = world["x"]
    mirror = x / ".claude/skills"
    shutil.copytree(x / "skills", mirror)
    for name in SKILLS:  # as the pre-fix case-insensitive install left it
        (mirror / name / RECORD_FILENAME).write_text(
            f"schema=kip.skill-install.v1\nskill={name}\ndeployment={x.resolve()}\nversion=1.0.0\n"
            "client=claude\nscope=project\ninstalled_at=2026-09-13T00:00:00Z\n"
        )
    target = _spelling(x, tmp_path, spelling)

    result = _run(["/bin/bash", str(x / "scripts/uninstall-agent-files.sh"), "project", str(target)], world["home"])

    assert result.returncode == 0, result.stderr + result.stdout
    assert "inside the deployment itself" in result.stdout
    for name in SKILLS:
        assert (mirror / name / "SKILL.md").is_file()


def test_uninstall_keeps_the_pointer_so_legacy_project_copies_keep_resolving(world: dict[str, Path]) -> None:
    home, x = world["home"], world["x"]
    legacy = world["project-b"] / ".claude/skills"
    for name in SKILLS:
        shutil.copytree(ROOT / "skills" / name, legacy / name, ignore=shutil.ignore_patterns(".DS_Store"))
    (home / ".config/kip").mkdir(parents=True)
    (home / ".config/kip/project-root").write_text(f"{x}\n")
    _install(x, home, "project", str(world["project-a"]))

    result = _run(["/bin/bash", str(x / "scripts/uninstall-agent-files.sh"), "project", str(world["project-a"]), "--client", "claude"], home)

    assert result.returncode == 0, result.stderr + result.stdout
    assert not (world["project-a"] / ".claude/skills/kip-setup").exists()
    assert (home / ".config/kip/project-root").read_text() == f"{x}\n"
    assert f"remove it with: rm {home / '.config/kip/project-root'}" in result.stdout
    resolved = _resolve(legacy, home, world["unrelated"])
    assert resolved.stdout.splitlines() == ["deployment-x", "capabilities"], resolved.stderr


def test_uninstall_reports_an_unreadable_pointer(world: dict[str, Path]) -> None:
    home = world["home"]
    (home / ".config/kip").mkdir(parents=True)
    (home / ".config/kip/project-root").write_bytes(b"\xff\xfe")
    result = _run(["/bin/bash", str(world["x"] / "scripts/uninstall-agent-files.sh"), "personal"], home)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "could not be read" in result.stdout
    assert (home / ".config/kip/project-root").read_bytes() == b"\xff\xfe"


def test_refresh_run_by_hand_adopts_a_legacy_personal_install(world: dict[str, Path]) -> None:
    # An archive upgrade from 3.15.0 finishes with the old in-memory upgrade.sh,
    # which has no refresh; the documented one-time --refresh must adopt.
    home, x = world["home"], world["x"]
    skills = _legacy_personal_install(home, x)

    result = _run(["/bin/bash", str(x / "scripts/install-agent-files.sh"), "--refresh"], home)

    assert result.returncode == 0, result.stderr + result.stdout
    assert f"Adopted {skills}" in result.stdout
    for name in SKILLS:
        record = read_install_record(skills / name)
        assert record is not None and (record.deployment, record.version) == (x.resolve(), "1.0.0")
    assert [item.destination for item in find_recorded_installs(x)] == [skills.resolve()]
    assert (home / ".config/kip/project-root").read_text() == f"{x}\n"
