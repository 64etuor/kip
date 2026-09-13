"""Where a deployment's agent skills were installed, and whether they are current.

`scripts/install_agent_files.py` writes two things and this module only reads
them (standard library only, so `kip doctor` and the upgrade finish path can
use it before or without the project environment):

* an install record, `.kip-skill-install`, inside every installed skill
  directory: the deployment root and `VERSION` that copy was installed from;
* a registry, `var/skill-installs.json`, inside the deployment: every skills
  directory it installed into, so an upgrade can find and refresh them.

The repository's own `skills/` and `.claude/skills/` never carry a record.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

SKILL_NAMES: tuple[str, ...] = ("knowledge-fabric", "kip-setup")
RECORD_FILENAME = ".kip-skill-install"
RECORD_SCHEMA = "kip.skill-install.v1"
REGISTRY_PATH = Path("var/skill-installs.json")
REGISTRY_SCHEMA = "kip.skill-install-registry.v1"
# Skills directory relative to a project root or HOME, per agent client.
# Codex: $REPO_ROOT/.agents/skills and $HOME/.agents/skills (Codex "Build skills" docs).
CLIENT_SKILL_DIRS: dict[str, str] = {"claude": ".claude/skills", "codex": ".agents/skills"}

# SkillInstallStatus.state values.
CURRENT = "current"  # recorded for this deployment at its VERSION
STALE = "stale"  # recorded for this deployment at another version
MISSING = "missing"  # the skill directory no longer exists
OTHER_DEPLOYMENT = "other_deployment"  # recorded for a different deployment
UNRECORDED = "unrecorded"  # exists without a readable KIP record (foreign, symlink, or pre-record)


@dataclass(frozen=True)
class InstallRecord:
    skill: str
    deployment: Path
    version: str
    client: str
    scope: str
    installed_at: str


@dataclass(frozen=True)
class RegisteredLocation:
    destination: Path
    client: str
    scope: str


@dataclass(frozen=True)
class SkillInstallStatus:
    destination: Path
    client: str
    scope: str
    skill: str
    state: str
    installed_version: str | None
    deployment_version: str | None
    recorded_deployment: Path | None

    @property
    def ok(self) -> bool:
        return self.state == CURRENT


def format_install_record(record: InstallRecord) -> str:
    return (
        "# Written by KIP scripts/install_agent_files.py; reinstall instead of editing.\n"
        f"schema={RECORD_SCHEMA}\n"
        f"skill={record.skill}\n"
        f"deployment={record.deployment}\n"
        f"version={record.version}\n"
        f"client={record.client}\n"
        f"scope={record.scope}\n"
        f"installed_at={record.installed_at}\n"
    )


def parse_install_record(text: str) -> InstallRecord | None:
    """Parse a record; None when it is not a complete v1 record."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields.setdefault(key, value)
    required = ("skill", "deployment", "version", "client", "scope", "installed_at")
    if fields.get("schema") != RECORD_SCHEMA or not all(fields.get(key) for key in required):
        return None
    deployment = Path(fields["deployment"])
    if not deployment.is_absolute():
        return None
    return InstallRecord(
        skill=fields["skill"],
        deployment=deployment,
        version=fields["version"],
        client=fields["client"],
        scope=fields["scope"],
        installed_at=fields["installed_at"],
    )


def read_install_record(skill_dir: Path) -> InstallRecord | None:
    """The record of an installed skill directory; None for symlinks and foreign skills."""
    record = skill_dir / RECORD_FILENAME
    if skill_dir.is_symlink() or record.is_symlink() or not record.is_file():
        return None
    try:
        return parse_install_record(record.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def deployment_version(deployment: Path) -> str | None:
    try:
        version = (deployment / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return version or None


def same_path(first: Path, second: Path) -> bool:
    """Whether two spellings name one file: by identity when both exist, so a
    differently cased (case-insensitive filesystem) or symlinked spelling matches."""
    try:
        return os.path.samefile(first, second)
    except OSError:
        return first.resolve() == second.resolve()


def is_within(path: Path, root: Path) -> bool:
    """Whether path is root or lies under it, by file identity of each existing ancestor."""
    try:
        root_stat = root.stat()
    except OSError:
        return False
    for candidate in (path.absolute(), path.resolve()):
        for ancestor in (candidate, *candidate.parents):
            try:
                ancestor_stat = ancestor.stat()
            except OSError:
                continue
            if (ancestor_stat.st_dev, ancestor_stat.st_ino) == (root_stat.st_dev, root_stat.st_ino):
                return True
    return False


def format_registry(locations: list[RegisteredLocation]) -> str:
    payload = {
        "schema": REGISTRY_SCHEMA,
        "installs": [
            {"destination": str(item.destination), "client": item.client, "scope": item.scope}
            for item in sorted(locations, key=lambda item: str(item.destination))
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def find_recorded_installs(deployment: Path) -> tuple[RegisteredLocation, ...]:
    """Skills directories this deployment installed into, from its registry.

    An absent registry is empty. A registry that is not valid v1 JSON raises
    ValueError rather than being read as "nothing installed".
    """
    registry = deployment / REGISTRY_PATH
    if not registry.exists():
        return ()
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Skill install registry {registry} is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Skill install registry {registry} is not a {REGISTRY_SCHEMA} object")
    installs = payload.get("installs")
    if payload.get("schema") != REGISTRY_SCHEMA or not isinstance(installs, list):
        raise ValueError(f"Skill install registry {registry} is not {REGISTRY_SCHEMA}")
    locations: list[RegisteredLocation] = []
    for item in installs:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) and item.get(key) for key in ("destination", "client", "scope")
        ) or not Path(item["destination"]).is_absolute():
            raise ValueError(f"Skill install registry {registry} has a malformed entry")
        locations.append(RegisteredLocation(Path(item["destination"]), item["client"], item["scope"]))
    return tuple(locations)


def location_statuses(deployment: Path, location: RegisteredLocation) -> tuple[SkillInstallStatus, ...]:
    """One status per bundled skill at a registered location."""
    current_version = deployment_version(deployment)
    statuses: list[SkillInstallStatus] = []
    for skill in SKILL_NAMES:
        skill_dir = location.destination / skill
        record = read_install_record(skill_dir)
        if record is not None:
            if not same_path(record.deployment, deployment):
                state = OTHER_DEPLOYMENT
            elif record.version == current_version:
                state = CURRENT
            else:
                state = STALE
        elif skill_dir.is_symlink() or skill_dir.exists():
            state = UNRECORDED
        else:
            state = MISSING
        statuses.append(
            SkillInstallStatus(
                destination=location.destination,
                client=location.client,
                scope=location.scope,
                skill=skill,
                state=state,
                installed_version=record.version if record else None,
                deployment_version=current_version,
                recorded_deployment=record.deployment if record else None,
            )
        )
    return tuple(statuses)


def skill_install_statuses(deployment: Path) -> tuple[SkillInstallStatus, ...]:
    """Every registered install of this deployment, per skill. Raises ValueError like find_recorded_installs."""
    return tuple(
        status
        for location in find_recorded_installs(deployment)
        for status in location_statuses(deployment, location)
    )
