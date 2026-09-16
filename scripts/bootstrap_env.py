#!/usr/bin/env python3
"""Create a private local dotenv once; never replace deployment credentials."""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

# Host ports compose.yaml publishes by default, and where a second deployment on
# the same machine starts looking for free ones. KIP_SEMANTIC_PORT is left
# alone: every deployment on a machine shares one model runtime.
DEFAULT_PORTS = {"KIP_POSTGRES_PORT": 5432, "KIP_API_PORT": 8080}
AUTOMATIC_PORT_FLOORS = {"KIP_POSTGRES_PORT": 55432, "KIP_API_PORT": 18080}
DEFAULT_COMPOSE_PROJECT = "kip"
COMPOSE_PROJECT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
PROJECT_NAME_CANDIDATES = 100
COMPOSE_PS_FORMAT = (
    '{{.Label "com.docker.compose.project"}}\t'
    '{{.Label "com.docker.compose.project.working_dir"}}\t{{.Ports}}'
)
# Compose labels a volume with its project, never with the directory that
# created it, so a volume is attributed only through its project's containers.
COMPOSE_VOLUME_FORMAT = '{{.Label "com.docker.compose.project"}}\t{{.Name}}'
# scripts/prerequisites.py's "Action required" status, which bootstrap.sh
# passes through: the operator must act before bootstrap can continue.
EXIT_ACTION_REQUIRED = 75

# Every KIP_POSTGRES_IMAGE value an earlier release shipped in .env.example or
# as the compose.yaml default (`git log -p -- .env.example compose.yaml`). An
# existing .env holding one was written by KIP, not chosen by the operator, and
# it overrides compose.yaml's newer default, so it is replaced with the current
# pin. Add the outgoing value whenever the pin changes; tests/test_bootstrap_env.py
# compares this set with the git history.
SHIPPED_POSTGRES_IMAGES = frozenset({
    "pgvector/pgvector:0.8.2-pg18-trixie",
    "pgvector/pgvector:0.8.2-pg18-trixie@sha256:b7337db8fe39d12fe8ecb0003c72680f24479813a744b43154eee6f2eab5a5f3",
})
POSTGRES_IMAGE_LINE = re.compile(r"^(\s*(?:export\s+)?KIP_POSTGRES_IMAGE\s*=)(.*?)(\r?\n)?$")

# Credentials the Compose application roles need. An upgraded deployment keeps
# its own .env, so these are appended when missing: without them every
# `docker compose -f compose.yaml ...` call fails interpolation, and the API
# and worker would fall back to nothing at all.
APPLICATION_ROLE_PASSWORDS = {
    "KIP_API_DB_PASSWORD": "kip_api",
    "KIP_WORKER_DB_PASSWORD": "kip_worker",
    "KIP_BACKUP_DB_PASSWORD": "kip_backup",
}


def _write_private(root: Path, target: Path, text: str) -> bool:
    """Materialize `text` at `target` without ever clobbering an existing file."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.bootstrap-", dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            return False
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _replace_file(root: Path, target: Path, data: bytes) -> OSError | None:
    """Replace `target` with `data` through a renamed temporary file.

    The mode is kept, and the owner and group where the process may set them.
    The rename splits a hard link: other names keep the previous content.
    Returns the error instead of raising when the file cannot be replaced.
    """
    temporary: Path | None = None
    try:
        status = target.stat()
        descriptor, temporary_name = tempfile.mkstemp(prefix=".env.update-", dir=root)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, status.st_mode & 0o777)
        # only root may give a file away; the new file keeps this user's ids
        with contextlib.suppress(OSError):
            os.chown(temporary, status.st_uid, status.st_gid)
        os.replace(temporary, target)
    except BaseException as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if isinstance(error, OSError):
            return error
        raise
    return None


def _assignments(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value.strip().strip("'\"")
    return values


def _backup_url(database_url: str, password: str) -> str | None:
    """The existing owner URL with the kip_backup login substituted in."""
    if not database_url.startswith(("postgresql://", "postgres://")):
        return None
    parts = urlsplit(database_url)
    if not parts.hostname:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    location = f"kip_backup:{quote(password, safe='')}@{host}"
    if parts.port:
        location = f"{location}:{parts.port}"
    return urlunsplit((parts.scheme, location, parts.path, parts.query, parts.fragment))


def _append_application_role_credentials(root: Path, target: Path) -> bool:
    raw = target.read_bytes()
    text = raw.decode("utf-8")
    newline = "\r\n" if b"\r\n" in raw else "\n"
    present = _assignments(text)
    added: list[str] = []
    passwords: dict[str, str] = {}
    for name in APPLICATION_ROLE_PASSWORDS:
        passwords[name] = present.get(name) or secrets.token_hex(32)
        if name not in present:
            added.append(f"{name}={passwords[name]}")
    backup_url_added = False
    if "KIP_BACKUP_DATABASE_URL" not in present:
        url = _backup_url(present.get("KIP_DATABASE_URL", ""), passwords["KIP_BACKUP_DB_PASSWORD"])
        if url is not None:
            added.append(f"KIP_BACKUP_DATABASE_URL={url}")
            backup_url_added = True
    if not added:
        return False
    header = [
        "",
        "# Added by scripts/bootstrap_env.py. POSTGRES_USER is the image bootstrap",
        "# role, which PostgreSQL creates as a SUPERUSER with BYPASSRLS and which",
        "# therefore bypasses every row level security policy. The compose `roles`",
        "# service gives these non-superuser logins a password so the api and",
        "# worker containers stop using the owner; backup keeps BYPASSRLS because",
        "# it must read every workspace. Existing values are never replaced.",
    ]
    updated = text if text.endswith("\n") else text + newline
    updated += newline.join(header + added) + newline
    names = ", ".join(entry.split("=", 1)[0] for entry in added)
    error = _replace_file(root, target, updated.encode("utf-8"))
    if error is not None:
        print(f"Warning: could not add {names} to {target} ({error.strerror or error}); add them by hand.", file=sys.stderr)
        return False
    print(f"Added {names} to .env for the non-owner application roles.", file=sys.stderr)
    if not backup_url_added and "KIP_BACKUP_DATABASE_URL" not in present:
        print(
            "KIP_DATABASE_URL is not a PostgreSQL URL; set KIP_BACKUP_DATABASE_URL "
            "for the kip_backup login by hand before running scripts/backup.sh.",
            file=sys.stderr,
        )
    return True


def _dotenv_value(raw: str) -> str:
    """A dotenv value as scripts/load_dotenv.py reads it: quotes or a " #" comment removed."""
    value = raw.strip()
    if value[:1] in {"'", '"'}:
        end = value.find(value[0], 1)
        return value[1:end] if end > 0 else value
    comment = value.find(" #")
    return value[:comment].rstrip() if comment >= 0 else value


def _example_postgres_image(source: Path) -> str | None:
    """KIP_POSTGRES_IMAGE in an .env.example, or in the one inside a package ZIP."""
    try:
        if zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as archive:
                names = [
                    name for name in archive.namelist()
                    if name == ".env.example" or (name.count("/") == 1 and name.endswith("/.env.example"))
                ]
                if len(names) != 1:
                    return None
                text = archive.read(names[0]).decode("utf-8")
        else:
            text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile):
        return None
    for line in text.splitlines():
        match = POSTGRES_IMAGE_LINE.match(line)
        if match:
            return _dotenv_value(match.group(2)) or None
    return None


def _refresh_postgres_image(root: Path, target: Path, example: Path, *, write: bool) -> None:
    """Replace a KIP-shipped KIP_POSTGRES_IMAGE in an existing .env with the current pin."""
    current = _example_postgres_image(example)
    if current is None:
        return
    text = target.read_bytes().decode("utf-8")
    lines = text.splitlines(keepends=True)
    found = [(index, match) for index, line in enumerate(lines) if (match := POSTGRES_IMAGE_LINE.match(line))]
    if not found:
        return  # compose.yaml's default applies
    if len(found) > 1:
        print(
            f"Warning: {target} assigns KIP_POSTGRES_IMAGE {len(found)} times, so it was left unchanged. "
            f"This release pins {current}; keep one KIP_POSTGRES_IMAGE line.",
            file=sys.stderr,
        )
        return
    index, match = found[0]
    value = _dotenv_value(match.group(2))
    if value == current:
        return
    installed = _example_postgres_image(root / ".env.example")
    if value not in SHIPPED_POSTGRES_IMAGES and value != installed:
        print(
            f"Warning: {target} sets KIP_POSTGRES_IMAGE={value}, which is not an image KIP shipped, so it "
            f"was left unchanged. This release pins {current}. To use it, set KIP_POSTGRES_IMAGE={current} "
            "in .env (or the same image from your registry), then run ./scripts/app-up.sh and "
            "./scripts/migrate.sh.",
            file=sys.stderr,
        )
        return
    if not write:
        print(f"Would update KIP_POSTGRES_IMAGE in {target}: {value} -> {current}. Nothing was written.", file=sys.stderr)
        return
    if target.is_symlink():
        print(
            f"Warning: {target} is a symlink, so KIP_POSTGRES_IMAGE={value} was left unchanged. "
            f"Set KIP_POSTGRES_IMAGE={current} in the file it points to.",
            file=sys.stderr,
        )
        return
    lines[index] = match.group(1) + match.group(2).replace(value, current, 1) + (match.group(3) or "")
    error = _replace_file(root, target, "".join(lines).encode("utf-8"))
    if error is not None:
        print(
            f"Warning: could not update KIP_POSTGRES_IMAGE in {target} ({error.strerror or error}); "
            f"set KIP_POSTGRES_IMAGE={current} there by hand.",
            file=sys.stderr,
        )
        return
    print(
        f"Updated KIP_POSTGRES_IMAGE in {target}: {value} -> {current}. The running PostgreSQL container "
        "keeps the old image until the next ./scripts/app-up.sh, which pulls the new one and restarts "
        "PostgreSQL on the same volume; ./scripts/migrate.sh then updates the vector extension.",
        file=sys.stderr,
    )


def _exported_port(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    if not (raw.isascii() and raw.isdigit() and 1 <= int(raw) <= 65535):
        raise SystemExit(
            f"error: {name}={os.environ[name]!r} is not a port; export an integer "
            "from 1 to 65535 or unset it, then rerun ./scripts/bootstrap.sh"
        )
    return int(raw)


def _exported_project_name() -> str | None:
    raw = os.environ.get("COMPOSE_PROJECT_NAME", "")
    if not raw:
        return None
    if not COMPOSE_PROJECT_NAME_PATTERN.fullmatch(raw):
        raise SystemExit(
            f"error: COMPOSE_PROJECT_NAME={raw!r} is not a Docker Compose project name; "
            "use lowercase letters, digits, '-' and '_', starting with a letter or digit, "
            "then rerun ./scripts/bootstrap.sh"
        )
    return raw


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _docker_rows(docker: str, arguments: list[str], fields: int) -> list[list[str]] | None:
    """Tab-separated `docker ... --format` rows, or None when Docker cannot be queried."""
    try:
        completed = subprocess.run(
            [docker, *arguments], capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    rows = []
    for line in completed.stdout.splitlines():
        if line.strip():
            cells = line.split("\t", fields - 1)
            rows.append(cells + [""] * (fields - len(cells)))
    return rows


def _docker_state() -> tuple[list[list[str]] | None, list[list[str]] | None]:
    """Compose containers (project, working dir, ports) and volumes (project, name)."""
    docker = shutil.which("docker")
    if docker is None:
        return None, None
    # Separate queries: a failed `ps` must not hide the volumes.
    return (
        _docker_rows(docker, ["ps", "--all", "--format", COMPOSE_PS_FORMAT], 3),
        _docker_rows(docker, ["volume", "ls", "--format", COMPOSE_VOLUME_FORMAT], 2),
    )


def _same_directory(value: str, root: Path) -> bool:
    # A symlinked or differently cased spelling of this directory matches.
    try:
        return bool(value) and os.path.samefile(value, root)
    except OSError:
        return False


def _publishes(published: str, port: int) -> bool:
    return any(
        int(first) <= port <= int(last or first)
        for first, last in re.findall(r":(\d+)(?:-(\d+))?->", published)
    )


def _directory_project_stem(root: Path) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", root.resolve().name.lower())
    base = re.sub(r"-{2,}", "-", base).strip("-_")
    return base if base.startswith("kip-") else f"kip-{base}" if base else "kip-deployment"


def _derived_project_names(root: Path) -> set[str]:
    """Every name `_directory_project_name` can give a deployment in `root`."""
    stem = _directory_project_stem(root)
    return {stem, *(f"{stem}-{index}" for index in range(2, PROJECT_NAME_CANDIDATES + 1))}


def _directory_project_name(root: Path, used: set[str]) -> str:
    stem = _directory_project_stem(root)
    # Two deployment directories can share a basename, and a name whose
    # volumes survive would hand this deployment another password's database.
    for index in range(1, PROJECT_NAME_CANDIDATES + 1):
        candidate = stem if index == 1 else f"{stem}-{index}"
        if candidate not in used:
            return candidate
    raise SystemExit(
        f"error: Docker Compose projects {stem} through {stem}-{PROJECT_NAME_CANDIDATES} all have "
        "containers or volumes on this machine; export COMPOSE_PROJECT_NAME=<unique-name> and "
        "rerun ./scripts/bootstrap.sh"
    )


def _free_port(floor: int, taken: set[int]) -> int:
    for port in range(floor, 65536):
        if port not in taken and not _listening(port):
            return port
    raise SystemExit(f"error: no free port at or above {floor} on 127.0.0.1")


def _refuse_missing_env(root: Path, projects: list[str], volumes: list[list[str]] | None) -> None:
    names = ", ".join(f'"{project}"' for project in projects)
    subject = f"project {names} has" if len(projects) == 1 else f"projects {names} have"
    owned = sorted(volume for project, volume in volumes or [] if project in projects)
    also = f" and volumes {', '.join(owned)}" if owned else ""
    print(
        f"Action required: this deployment's database already exists, but {root / '.env'} is missing.\n"
        f"Docker Compose {subject} containers created from {root}{also}.\n"
        "A new .env would carry a new random database password, which cannot open the existing "
        "database volume.\n"
        f"Restore {root / '.env'} from backup, then rerun ./scripts/bootstrap.sh. Nothing was written.",
        file=sys.stderr,
    )
    raise SystemExit(EXIT_ACTION_REQUIRED)


def _refuse_derived_volumes(root: Path, projects: list[str], volumes: list[list[str]]) -> None:
    names = ", ".join(f'"{project}"' for project in projects)
    subject = f"project {names} has" if len(projects) == 1 else f"projects {names} have"
    owned = sorted(volume for project, volume in volumes if project in projects)
    listed = " ".join(owned)
    stem = _directory_project_stem(root)
    # kip-foo-2 is the second name for a directory "foo" and the first name
    # for a directory "foo-2", so such volumes cannot be attributed.
    ambiguous = "".join(
        f'A deployment in a directory named "{project.removeprefix("kip-")}" is also named "{project}", '
        "so those volumes may belong to it instead.\n"
        for project in projects if project != stem
    )
    print(
        f"Action required: this deployment's database may already exist, but {root / '.env'} is missing.\n"
        f"Docker Compose {subject} volumes {', '.join(owned)} but no containers. Bootstrap names a "
        f'deployment in {root} "{stem}" (or "{stem}-N" when that name is taken), so they may be this '
        "deployment's database, stopped with ./scripts/app-up.sh --down.\n"
        f"{ambiguous}"
        "A new .env would carry a new random database password, which cannot open that volume, or "
        "another project name with an empty database. Nothing was written.\n"
        f"- This deployment's database: restore {root / '.env'} from backup, then rerun ./scripts/bootstrap.sh.\n"
        "- Another deployment's volumes: export COMPOSE_PROJECT_NAME=<unique-name> (not one of these "
        "names) and rerun ./scripts/bootstrap.sh; it still chooses free ports when the defaults are taken.\n"
        f"- Leftovers with no .env to restore: inspect them with docker volume inspect {listed}; once their "
        f"data is not needed, remove them with docker volume rm {listed} and rerun ./scripts/bootstrap.sh.",
        file=sys.stderr,
    )
    raise SystemExit(EXIT_ACTION_REQUIRED)


def _deployment_values(
    root: Path, *, detect: bool,
) -> tuple[dict[str, tuple[str, str]], str, list[str]]:
    """The project name and ports a new .env carries, each with its reason.

    Exported values always win, including a port that is already in use.
    When bootstrap asked for detection, a busy default port makes this
    deployment choose a free one and, unless a project name is exported,
    another project's containers or volumes make it choose its own name.
    Returns the values, what was found, and notes.
    """
    ports = {name: _exported_port(name) for name in DEFAULT_PORTS}
    values = {name: (str(port), "exported") for name, port in ports.items() if port is not None}
    exported_name = _exported_project_name()
    if exported_name is not None:
        values["COMPOSE_PROJECT_NAME"] = (exported_name, "exported")
    if not detect or (exported_name is not None and None not in ports.values()):
        return values, "", []
    # An exported project name is the operator's choice, so Docker is not asked
    # about projects; default ports that are taken are still replaced.
    containers, volumes = _docker_state() if exported_name is None else (None, None)
    own = sorted({
        project for project, directory, _ in containers or []
        if project and _same_directory(directory, root)
    })
    if own:
        # This directory's stack exists and only .env is gone: new values
        # would silently point the CLI and MCP at a new, empty database.
        _refuse_missing_env(root, own, volumes)
    # Volumes of a name bootstrap derives for this directory, with no
    # containers from another directory, are most likely this deployment's
    # own database stopped with --down: a new .env would miss it.
    elsewhere = {project for project, directory, _ in containers or [] if directory}
    derived = sorted({
        project for project, _ in volumes or []
        if project in _derived_project_names(root) and project not in elsewhere
    })
    if derived:
        _refuse_derived_volumes(root, derived, volumes or [])
    found: list[str] = []
    notes: list[str] = []
    others = sorted({
        directory for project, directory, _ in containers or []
        if project == DEFAULT_COMPOSE_PROJECT and directory
    })
    if others:
        found.append(
            f'Docker Compose project "{DEFAULT_COMPOSE_PROJECT}" has containers from {", ".join(others)}'
        )
    unattributed = sorted(
        volume for project, volume in volumes or [] if project == DEFAULT_COMPOSE_PROJECT
    )
    if unattributed and not others:
        found.append(
            f'Docker Compose project "{DEFAULT_COMPOSE_PROJECT}" has volumes '
            f"({', '.join(unattributed)}) but no containers, and Docker does not record which "
            "directory created them"
        )
        notes.append(
            "If those volumes are this deployment's own database (stopped with "
            "./scripts/app-up.sh --down), do not use these values: restore its .env from backup."
        )
    for name, default in DEFAULT_PORTS.items():
        port = ports[name] or default
        if not _listening(port):
            continue
        if ports[name] is not None:
            values[name] = (str(port), "exported, although already in use")
        holder = next(
            ((project, directory) for project, directory, published in containers or []
             if project and _publishes(published, port)),
            None,
        )
        if holder is None:
            found.append(f"127.0.0.1:{port} ({name}) is already in use")
        else:
            found.append(
                f'127.0.0.1:{port} ({name}) is published by Docker Compose project "{holder[0]}" '
                f"at {holder[1]}"
            )
    if not found:
        return values, "", []
    header = (
        "Another KIP deployment is on this machine" if others
        else "The default Docker Compose project or host ports are taken on this machine"
    )
    used = {project for project, _, _ in containers or []} | {project for project, _ in volumes or []}
    if exported_name is None:
        values["COMPOSE_PROJECT_NAME"] = (
            _directory_project_name(root, used), "derived from the deployment directory",
        )
    taken = {port for port in ports.values() if port is not None}
    for name, floor in AUTOMATIC_PORT_FLOORS.items():
        if ports[name] is None:
            port = _free_port(floor, taken)
            taken.add(port)
            values[name] = (str(port), f"first free port at or above {floor}")
    return values, f"{header}: {'; '.join(found)}.", notes


def _with_port(url: str, port: int) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"postgresql", "postgres"} or not parts.hostname:
        return url
    userinfo, at, _ = parts.netloc.rpartition("@")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    location = f"{userinfo}{at}{host}:{port}"
    return urlunsplit((parts.scheme, location, parts.path, parts.query, parts.fragment))


def _with_values(text: str, values: dict[str, tuple[str, str]]) -> str:
    for name in DEFAULT_PORTS:
        if name in values:
            text, count = re.subn(
                rf"^{name}=.*$", lambda _, name=name: f"{name}={values[name][0]}",
                text, flags=re.MULTILINE,
            )
            if count != 1:
                raise SystemExit(f"error: .env.example must assign {name} exactly once")
    if "KIP_POSTGRES_PORT" in values:
        port = int(values["KIP_POSTGRES_PORT"][0])
        text = re.sub(
            r"^KIP_DATABASE_URL=(.*)$",
            lambda match: f"KIP_DATABASE_URL={_with_port(match.group(1), port)}",
            text, flags=re.MULTILINE,
        )
    if "COMPOSE_PROJECT_NAME" in values:
        text = (
            "# Docker Compose project: this deployment's own containers and volumes.\n"
            f"COMPOSE_PROJECT_NAME={values['COMPOSE_PROJECT_NAME'][0]}\n\n" + text
        )
    return text


def _report_values(values: dict[str, tuple[str, str]], found: str, notes: list[str]) -> None:
    if not values:
        return
    if found:
        print(
            found + "\nThis .env is new and has no data yet, so bootstrap chose values that do "
            "not collide:",
            file=sys.stderr,
        )
    else:
        print("Bootstrap wrote the exported values into the new .env:", file=sys.stderr)
    for name in ("COMPOSE_PROJECT_NAME", *DEFAULT_PORTS):
        if name in values:
            value, reason = values[name]
            also = " (also in KIP_DATABASE_URL and KIP_BACKUP_DATABASE_URL)" if name == "KIP_POSTGRES_PORT" else ""
            print(f"  {name}={value}{also} [{reason}]", file=sys.stderr)
    if found:
        print(
            "Confirm these values before ./scripts/app-up.sh. KIP_SEMANTIC_PORT is unchanged: "
            "the machine keeps one model runtime.",
            file=sys.stderr,
        )
    for note in notes:
        print(note, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--detect-existing-deployment", action="store_true",
        help="unless COMPOSE_PROJECT_NAME is exported, check Docker and the default ports and "
        "choose a project name and free ports on a collision",
    )
    parser.add_argument(
        "--refresh-postgres-image", action="store_true",
        help="only replace an existing .env's KIP_POSTGRES_IMAGE when an earlier KIP release shipped "
        "that value; any other value is reported and kept",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="with --refresh-postgres-image: report, never write",
    )
    parser.add_argument(
        "--example", type=Path,
        help="the .env.example, or a package ZIP holding one, whose KIP_POSTGRES_IMAGE is current "
        "(default: ROOT/.env.example)",
    )
    arguments = parser.parse_args(argv)
    root: Path = arguments.root
    target = root / ".env"
    example: Path = arguments.example or root / ".env.example"
    if arguments.dry_run and not arguments.refresh_postgres_image:
        parser.error("--dry-run needs --refresh-postgres-image")
    if arguments.refresh_postgres_image:
        if target.is_file():
            _refresh_postgres_image(root, target, example, write=not arguments.dry_run)
        return 0
    if target.exists() or target.is_symlink():
        if target.is_file():
            _append_application_role_credentials(root, target)
            _refresh_postgres_image(root, target, example, write=True)
        return 0
    values, found, notes = _deployment_values(root, detect=arguments.detect_existing_deployment)
    text = (root / ".env.example").read_text(encoding="utf-8")
    # One fresh secret per placeholder, not per occurrence: a placeholder that
    # appears both as a variable and inside a URL must keep the same value.
    for placeholder in (
        "change-me-before-use",
        "replace-with-a-long-random-secret",
        "replace-with-a-different-long-random-secret",
        "replace-with-the-api-role-database-password",
        "replace-with-the-worker-role-database-password",
        "replace-with-the-backup-role-database-password",
    ):
        text = text.replace(placeholder, secrets.token_hex(32))
    text = _with_values(text, values)
    if not _write_private(root, target, text):
        return 0
    print("Created private .env with random local database and API credentials.", file=sys.stderr)
    # The backup URL carries a password, so .env.example cannot ship it; derive
    # it here from the freshly generated owner URL and kip_backup password,
    # which already carries the chosen PostgreSQL port.
    _append_application_role_credentials(root, target)
    _report_values(values, found, notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
