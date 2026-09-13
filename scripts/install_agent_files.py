"""Install, refresh or remove KIP's portable skill bundles.

  install_agent_files.py [personal | project [DIR]] [--client claude|codex|all]
  install_agent_files.py --uninstall [personal | project [DIR]] [--client claude|codex|all]
  install_agent_files.py --refresh

Each installed skill directory gets an install record naming this deployment
and its VERSION, and the deployment's var/skill-installs.json registers the
location so an upgrade can refresh it (--refresh). Both skills are staged
together and the old trees are restored on a handled failure.
"""
from __future__ import annotations

import argparse
import fcntl
import shlex
import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The upgrade finish path may run this with a bare python3 before the project
# environment exists; kip.skill_installs is standard-library only.
sys.path.insert(0, str(ROOT / "src"))

from kip.skill_installs import (  # noqa: E402
    CLIENT_SKILL_DIRS,
    MISSING,
    OTHER_DEPLOYMENT,
    RECORD_FILENAME,
    REGISTRY_PATH,
    SKILL_NAMES,
    UNRECORDED,
    InstallRecord,
    RegisteredLocation,
    deployment_version,
    find_recorded_installs,
    format_install_record,
    format_registry,
    is_within,
    location_statuses,
    read_install_record,
    same_path,
)

SKILLS = SKILL_NAMES
LEGACY_POINTER = Path(".config/kip/project-root")


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _refuse_deployment_itself(destination: Path, deployment: Path) -> None:
    # Compared by file identity, so a differently cased or symlinked spelling of
    # the deployment is refused too. The deployment resolves itself by walking
    # up, and its .claude/skills is the package-owned mirror of skills/ that
    # verification keeps byte-identical; an install record there would break both.
    if is_within(destination, deployment):
        raise ValueError(f"Refusing to install into the deployment itself ({destination}); install into a project or personal location")


def install_skills(
    source: Path, destination: Path, deployment: Path, client: str, scope: str, *, refresh: bool = False,
) -> None:
    """Install both bundles. With refresh=True every skill there must still carry
    this deployment's record when the lock is held, or nothing is replaced."""
    version = deployment_version(deployment)
    if version is None:
        raise ValueError(f"Deployment has no VERSION file: {deployment}")
    _refuse_deployment_itself(destination, deployment)
    for name in SKILLS:
        origin, target = source / name, destination / name
        if not (origin / "SKILL.md").is_file():
            raise ValueError(f"Missing source SKILL.md: {name}")
        if origin.is_symlink() or any(path.is_symlink() for path in origin.rglob("*")):
            raise ValueError(f"Source skill contains a symlink: {name}")
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise ValueError(f"Skill destination is a symlink or non-directory: {name}")
        if origin.resolve() == target.resolve():
            raise ValueError("Cannot install a skill over its source")
    registry = deployment / REGISTRY_PATH
    registry.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    with _locked(registry.with_suffix(".lock")), _locked(destination / ".kip-install.lock"):
        _refuse_deployment_itself(destination, deployment)
        _check_ownership(destination, deployment, refresh)
        _replace_skills(source, destination, deployment, version, client, scope)


def _check_ownership(destination: Path, deployment: Path, refresh: bool) -> None:
    for name in SKILLS:
        target = destination / name
        record = read_install_record(target)
        if refresh:
            if not (target.is_symlink() or target.exists()):
                raise ValueError(f"{target} was removed; refresh does not recreate it")
            if record is None:
                raise ValueError(f"{target} no longer carries a KIP install record")
            if not same_path(record.deployment, deployment):
                raise ValueError(f"{target} now belongs to deployment {record.deployment}")
        elif record is not None and not same_path(record.deployment, deployment):
            print(f"Warning: replacing {target}, which was installed from deployment {record.deployment}", file=sys.stderr)


def _replace_skills(source: Path, destination: Path, deployment: Path, version: str, client: str, scope: str) -> None:
    registry = deployment / REGISTRY_PATH
    stage = Path(tempfile.mkdtemp(prefix=".kip-install-", dir=destination))
    backups: list[str] = []
    installed: list[str] = []
    registry_temp: Path | None = None
    installed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017 - bare python3 may predate datetime.UTC
    try:
        for name in SKILLS:
            shutil.copytree(source / name, stage / name)
            record = InstallRecord(name, deployment.resolve(), version, client, scope, installed_at)
            (stage / name / RECORD_FILENAME).write_text(format_install_record(record), encoding="utf-8")
        locations = [item for item in find_recorded_installs(deployment) if not same_path(item.destination, destination)]
        locations.append(RegisteredLocation(destination.resolve(), client, scope))
        with tempfile.NamedTemporaryFile(mode="w", dir=registry.parent, delete=False, encoding="utf-8") as stream:
            registry_temp = Path(stream.name)
            stream.write(format_registry(locations))
        for name in SKILLS:
            target = destination / name
            if target.exists():
                target.replace(stage / f"{name}.previous")
                backups.append(name)
            (stage / name).replace(target)
            installed.append(name)
        registry_temp.replace(registry)
    except BaseException:
        # If rollback itself fails, leave the stage (and its originals) intact.
        for name in reversed(installed):
            shutil.rmtree(destination / name)
        for name in reversed(backups):
            (stage / f"{name}.previous").replace(destination / name)
        shutil.rmtree(stage)
        raise
    else:
        shutil.rmtree(stage)
    finally:
        if registry_temp is not None:
            registry_temp.unlink(missing_ok=True)


def _frontmatter_name(skill_md: Path) -> str | None:
    try:
        lines = skill_md.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        if line.startswith("name:"):
            return line[len("name:"):].strip().strip("\"'")
    return None


def _read_pointer(home: Path) -> str | None:
    """The legacy pointer's text; "" when absent, None when unreadable."""
    pointer = home / LEGACY_POINTER
    try:
        return pointer.read_text(encoding="utf-8").strip() if pointer.is_file() else ""
    except (OSError, UnicodeDecodeError):
        return None


def _legacy_personal_blocker(deployment: Path, home: Path) -> str | None:
    """Why ~/.claude/skills cannot be adopted; None when it can. "" means not ours to mention."""
    recorded = _read_pointer(home)
    if not recorded or not same_path(Path(recorded), deployment):
        return ""
    for name in SKILLS:
        skill_dir = home / CLIENT_SKILL_DIRS["claude"] / name
        if skill_dir.is_symlink() or not skill_dir.is_dir():
            return f"{skill_dir} is missing, a symlink or not a directory"
        if (skill_dir / RECORD_FILENAME).exists():
            return f"{skill_dir} already carries an install record"
        if _frontmatter_name(skill_dir / "SKILL.md") != name:
            return f"{skill_dir}/SKILL.md does not declare name: {name}"
    return None


def _adopt_legacy_personal(source: Path, deployment: Path, home: Path) -> tuple[Path | None, int]:
    """Adopt the one location 3.15.0 and earlier left a trace of: personal Claude skills
    plus ~/.config/kip/project-root naming this deployment. Project copies left no trace."""
    destination = home / CLIENT_SKILL_DIRS["claude"]
    blocker = _legacy_personal_blocker(deployment, home)
    if blocker is not None:
        if blocker:
            print(f"Not adopting {destination}: {blocker}")
        return None, 0
    try:
        install_skills(source, destination, deployment, "claude", "personal")
    except (OSError, ValueError) as exc:
        print(f"Failed to adopt {destination}: {exc}", file=sys.stderr)
        return None, 1
    # The pointer stays: record-less project copies from 3.15.0 and earlier
    # still resolve through it.
    print(f"Adopted {destination}: installed before install records; reinstalled at {deployment_version(deployment)} and registered")
    return destination, 0


def refresh_installs(source: Path, deployment: Path, home: Path) -> int:
    """Re-install every registered location where every skill still carries this
    deployment's record, after adopting a legacy personal install.

    Never creates a location or a skill. Returns 1 when a refresh failed, 0 otherwise.
    """
    version = deployment_version(deployment)
    try:
        locations = find_recorded_installs(deployment)
    except ValueError as exc:
        print(f"Skill refresh failed: {exc}", file=sys.stderr)
        return 1
    status = 0
    if not any(same_path(item.destination, home / CLIENT_SKILL_DIRS["claude"]) for item in locations):
        adopted, status = _adopt_legacy_personal(source, deployment, home)
        if adopted is None and not locations:
            print("No recorded skill installs to refresh.")
    for location in locations:
        destination = location.destination
        if not destination.is_dir():
            print(f"Skipped {destination}: the location was removed")
            continue
        if is_within(destination, deployment):
            print(f"Skipped {destination}: it is inside the deployment itself")
            continue
        states = location_statuses(deployment, location)
        other = next((item for item in states if item.state == OTHER_DEPLOYMENT), None)
        foreign = next((item for item in states if item.state == UNRECORDED), None)
        missing = next((item for item in states if item.state == MISSING), None)
        if all(item.state == MISSING for item in states):
            print(f"Skipped {destination}: the KIP skills were removed from it")
        elif other is not None:
            print(f"Skipped {destination}: {other.skill} now belongs to deployment {other.recorded_deployment}")
        elif foreign is not None:
            print(f"Skipped {destination}: {foreign.skill} there carries no KIP install record")
        elif missing is not None:
            print(f"Skipped {destination}: {missing.skill} was removed from it")
        else:
            previous = ", ".join(sorted({item.installed_version for item in states if item.installed_version}))
            try:
                install_skills(source, destination, deployment, location.client, location.scope, refresh=True)
            except (OSError, ValueError) as exc:
                print(f"Failed to refresh {destination}: {exc}", file=sys.stderr)
                status = 1
            else:
                print(f"Refreshed {destination} ({previous} -> {version})")
    return status


def uninstall_skills(destination: Path, deployment: Path) -> None:
    """Remove this deployment's skill copies at destination and say what stayed and why."""
    if is_within(destination, deployment):
        print(f"Left {destination}: it is inside the deployment itself, whose own skills KIP never uninstalls")
        return
    registry = deployment / REGISTRY_PATH
    lock_dir = registry.parent if registry.parent.is_dir() else None
    with _optional_lock(lock_dir / "skill-installs.lock" if lock_dir else None), \
            _optional_lock(destination / ".kip-install.lock" if destination.is_dir() else None):
        if not destination.is_dir():
            print(f"Nothing at {destination}: no skills directory")
        for name in SKILLS if destination.is_dir() else ():
            target = destination / name
            record = read_install_record(target)
            if target.is_symlink():
                print(f"Left {target}: it is a symlink, which KIP never installs")
            elif not target.exists():
                print(f"Left {target}: not present")
            elif record is None:
                print(f"Left {target}: no KIP install record (not installed by KIP, or installed before records existed; remove it by hand if it is KIP's)")
            elif not same_path(record.deployment, deployment):
                print(f"Left {target}: installed from deployment {record.deployment}; uninstall it from there")
            else:
                shutil.rmtree(target)
                print(f"Removed {target}")
        try:
            locations = list(find_recorded_installs(deployment))
        except ValueError as exc:
            print(f"Left the registry entry for {destination}: {exc}")
        else:
            remaining = [item for item in locations if not same_path(item.destination, destination)]
            if len(remaining) != len(locations):
                _write_atomically(registry, format_registry(remaining))
                print(f"Removed the registry entry for {destination} from {registry}")


def _report_legacy_pointer(deployment: Path, home: Path) -> None:
    # Never removed automatically: record-less copies from 3.15.0 and earlier,
    # which no registry lists, still resolve through it.
    pointer = home / LEGACY_POINTER
    recorded = _read_pointer(home)
    if recorded is None:
        print(f"Left the legacy pointer {pointer}: it could not be read")
    elif recorded and same_path(Path(recorded), deployment):
        print(
            f"Left the legacy pointer {pointer}: skill copies installed by KIP 3.15.0 or earlier still resolve "
            f"through it. Once none remain, remove it with: rm {shlex.quote(str(pointer))}"
        )
    elif recorded:
        print(f"Left the legacy pointer {pointer}: it points at {recorded}, not this deployment")


@contextmanager
def _optional_lock(path: Path | None) -> Iterator[None]:
    if path is None:
        yield
    else:
        with _locked(path):
            yield


def _write_atomically(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as stream:
        temp = Path(stream.name)
        stream.write(text)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("personal", "project"), nargs="?")
    parser.add_argument("target", type=Path, nargs="?")
    parser.add_argument("--client", choices=(*CLIENT_SKILL_DIRS, "all"))
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--refresh", action="store_true", help="re-install recorded locations (upgrade)")
    action.add_argument("--uninstall", action="store_true", help="remove this deployment's recorded copies")
    args = parser.parse_args()
    if args.refresh:
        if args.mode or args.target or args.client:
            parser.error("--refresh takes no location or client")
        return refresh_installs(ROOT / "skills", ROOT, Path.home())
    mode = args.mode or "personal"
    if mode == "personal" and args.target is not None:
        parser.error("target is only supported in project mode")
    clients = list(CLIENT_SKILL_DIRS) if (args.client or ("all" if args.uninstall else "claude")) == "all" else [args.client or "claude"]
    home = Path.home()
    root = home if mode == "personal" else (args.target or Path.cwd()).absolute()
    status = 0
    for client in clients:
        destination = root / CLIENT_SKILL_DIRS[client]
        if args.uninstall:
            try:
                uninstall_skills(destination, ROOT)
            except OSError as exc:
                print(f"Skill removal failed at {destination}: {exc}", file=sys.stderr)
                status = 1
            continue
        try:
            install_skills(ROOT / "skills", destination, ROOT, client, mode)
        except (OSError, ValueError) as exc:
            print(f"Skill installation failed: {exc}", file=sys.stderr)
            status = 1
            continue
        for name in SKILLS:
            print(f"Installed {name} Skill for {client} at {destination / name}")
        print(f"Recorded deployment {ROOT} ({deployment_version(ROOT)}) in each skill and registered {destination} in {ROOT / REGISTRY_PATH}")
    if args.uninstall:
        _report_legacy_pointer(ROOT, home)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
