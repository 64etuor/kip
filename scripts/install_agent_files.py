"""Install complete portable skill bundles, restoring the old trees on failure."""
from __future__ import annotations

import argparse
import fcntl
import shutil
import sys
import tempfile
from pathlib import Path

SKILLS = ("knowledge-fabric", "kip-setup")


def install_skills(source: Path, destination: Path, pointer: Path, project: Path) -> None:
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
    destination.mkdir(parents=True, exist_ok=True)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    with (destination / ".kip-install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _replace_skills(source, destination, pointer, project)


def _replace_skills(source: Path, destination: Path, pointer: Path, project: Path) -> None:
    stage = Path(tempfile.mkdtemp(prefix=".kip-install-", dir=destination))
    backups: list[str] = []
    installed: list[str] = []
    pointer_temp: Path | None = None
    try:
        for name in SKILLS:
            shutil.copytree(source / name, stage / name)
        with tempfile.NamedTemporaryFile(mode="w", dir=pointer.parent, delete=False) as stream:
            pointer_temp = Path(stream.name)
            stream.write(f"{project}\n")
        for name in SKILLS:
            target = destination / name
            if target.exists():
                target.replace(stage / f"{name}.previous")
                backups.append(name)
            (stage / name).replace(target)
            installed.append(name)
        pointer_temp.replace(pointer)
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
        if pointer_temp is not None:
            pointer_temp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("personal", "project"), nargs="?", default="personal")
    parser.add_argument("target", type=Path, nargs="?")
    args = parser.parse_args()
    if args.mode == "personal" and args.target is not None:
        parser.error("target is only supported in project mode")
    project = Path(__file__).resolve().parents[1]
    root = Path.home() if args.mode == "personal" else (args.target or Path.cwd()).resolve()
    destination = root / ".claude/skills"
    pointer = Path.home() / ".config/kip/project-root"
    try:
        install_skills(project / "skills", destination, pointer, project)
    except (OSError, ValueError) as exc:
        print(f"Skill installation failed: {exc}", file=sys.stderr)
        return 1
    for name in SKILLS:
        print(f"Installed {name} Skill at {destination / name}")
    print(f"Recorded KIP root at {pointer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
