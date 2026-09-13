#!/usr/bin/env python3
"""Assert on the skill install records and registry an installed deployment wrote.

`scripts/e2e-install.sh` and `scripts/e2e-upgrade.sh` install KIP's agent
skills from a real deployment into a throwaway HOME and unrelated project
directories. This module reads what those installs left on disk, the per-copy
`.kip-skill-install` record and the deployment's `var/skill-installs.json`, and
fails loudly when it is not what the release promises (ADR-067).

It deliberately re-parses both formats from their documented shape instead of
importing `kip.skill_installs`: the check must catch a writer and a reader that
drifted together. Standard library only, like `kip_envelope.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILLS = ("knowledge-fabric", "kip-setup")
RECORD = ".kip-skill-install"
RECORD_SCHEMA = "kip.skill-install.v1"
REGISTRY = Path("var/skill-installs.json")
REGISTRY_SCHEMA = "kip.skill-install-registry.v1"


class CheckFailed(Exception):
    """One assertion about an installed skill location did not hold."""


def _same(first: Path, second: Path) -> bool:
    return first.resolve() == second.resolve()


def read_record(skill_dir: Path) -> dict[str, str]:
    path = skill_dir / RECORD
    if not path.is_file():
        raise CheckFailed(f"{skill_dir} carries no {RECORD}")
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields.setdefault(key, value)
    if fields.get("schema") != RECORD_SCHEMA:
        raise CheckFailed(f"{path}: schema is {fields.get('schema')!r}, expected {RECORD_SCHEMA!r}")
    return fields


def check_record(args: argparse.Namespace) -> None:
    location = Path(args.location)
    for skill in SKILLS:
        skill_dir = location / skill
        if not (skill_dir / "SKILL.md").is_file():
            raise CheckFailed(f"{skill_dir} has no SKILL.md: the skill is not installed there")
        fields = read_record(skill_dir)
        expected = {"skill": skill, "version": args.version}
        if args.client:
            expected["client"] = args.client
        if args.scope:
            expected["scope"] = args.scope
        for key, value in expected.items():
            if fields.get(key) != value:
                raise CheckFailed(f"{skill_dir}/{RECORD}: {key} is {fields.get(key)!r}, expected {value!r}")
        recorded = fields.get("deployment") or ""
        if not recorded or not Path(recorded).is_absolute() or not _same(Path(recorded), Path(args.deployment)):
            raise CheckFailed(f"{skill_dir}/{RECORD}: deployment is {recorded!r}, expected {args.deployment!r}")
    print(f"record: {location} holds {', '.join(SKILLS)} at {args.version} from {args.deployment}")


def check_unrecorded(args: argparse.Namespace) -> None:
    location = Path(args.location)
    for skill in SKILLS:
        skill_dir = location / skill
        if not (skill_dir / "SKILL.md").is_file():
            raise CheckFailed(f"{skill_dir} has no SKILL.md: the unrecorded copy is gone")
        if (skill_dir / RECORD).exists():
            raise CheckFailed(f"{skill_dir} carries {RECORD}, but nothing should have recorded it")
    print(f"unrecorded: {location} holds {', '.join(SKILLS)} without install records")


def check_absent(args: argparse.Namespace) -> None:
    location = Path(args.location)
    present = [skill for skill in SKILLS if (location / skill).exists() or (location / skill).is_symlink()]
    if present:
        raise CheckFailed(f"{location} still holds {present}")
    print(f"absent: {location} holds none of {', '.join(SKILLS)}")


def check_registry(args: argparse.Namespace) -> None:
    path = Path(args.deployment) / REGISTRY
    if not path.is_file():
        raise CheckFailed(f"{path} does not exist")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != REGISTRY_SCHEMA:
        raise CheckFailed(f"{path}: schema is not {REGISTRY_SCHEMA!r}: {payload!r}")
    installs = payload.get("installs")
    if not isinstance(installs, list):
        raise CheckFailed(f"{path}: installs is not a list")
    listed = sorted(str(Path(item["destination"]).resolve()) for item in installs)
    expected = sorted(str(Path(item).resolve()) for item in args.expect)
    if listed != expected:
        raise CheckFailed(f"{path} lists {listed}, expected exactly {expected}")
    print(f"registry: {path} lists exactly {len(listed)} location(s)")


def set_version(args: argparse.Namespace) -> None:
    """Make a recorded copy stale the way an older install would have left it."""
    path = Path(args.location) / args.skill / RECORD
    read_record(path.parent)
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = [f"version={args.version}" if line.startswith("version=") else line for line in lines]
    if replaced == lines and f"version={args.version}" not in lines:
        raise CheckFailed(f"{path} has no version line to edit")
    path.write_text("\n".join(replaced) + "\n", encoding="utf-8")
    print(f"set-version: {path} now records version {args.version}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="Both skills carry a record for DEPLOYMENT at VERSION")
    record.add_argument("location", help="A skills directory, e.g. HOME/.claude/skills")
    record.add_argument("--deployment", required=True)
    record.add_argument("--version", required=True)
    record.add_argument("--client", default=None)
    record.add_argument("--scope", default=None)
    record.set_defaults(handler=check_record)

    unrecorded = subparsers.add_parser("unrecorded", help="Both skills are present and carry no record")
    unrecorded.add_argument("location")
    unrecorded.set_defaults(handler=check_unrecorded)

    absent = subparsers.add_parser("absent", help="Neither skill is present")
    absent.add_argument("location")
    absent.set_defaults(handler=check_absent)

    registry = subparsers.add_parser("registry", help="The deployment registry lists exactly these locations")
    registry.add_argument("deployment")
    registry.add_argument("--expect", action="append", default=[], metavar="LOCATION")
    registry.set_defaults(handler=check_registry)

    stale = subparsers.add_parser("set-version", help="Rewrite one record's version (test fixture)")
    stale.add_argument("location")
    stale.add_argument("--skill", choices=SKILLS, required=True)
    stale.add_argument("--version", required=True)
    stale.set_defaults(handler=set_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        arguments.handler(arguments)
    except (CheckFailed, OSError, ValueError, KeyError, TypeError) as failure:
        print(f"e2e skill install check failed: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
