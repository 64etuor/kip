#!/usr/bin/env python3
"""Create a private local dotenv once; never replace deployment credentials."""
from __future__ import annotations

import os
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

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
    text = target.read_text(encoding="utf-8")
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
    updated = text if text.endswith("\n") else text + "\n"
    updated += "\n".join(header + added) + "\n"
    mode = target.stat().st_mode & 0o777
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.append-", dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    names = ", ".join(entry.split("=", 1)[0] for entry in added)
    print(f"Added {names} to .env for the non-owner application roles.", file=sys.stderr)
    if not backup_url_added and "KIP_BACKUP_DATABASE_URL" not in present:
        print(
            "KIP_DATABASE_URL is not a PostgreSQL URL; set KIP_BACKUP_DATABASE_URL "
            "for the kip_backup login by hand before running scripts/backup.sh.",
            file=sys.stderr,
        )
    return True


def main() -> int:
    root = Path(sys.argv[1])
    target = root / ".env"
    if target.exists() or target.is_symlink():
        if target.is_file():
            _append_application_role_credentials(root, target)
        return 0
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
    if not _write_private(root, target, text):
        return 0
    print("Created private .env with random local database and API credentials.", file=sys.stderr)
    # The backup URL carries a password, so .env.example cannot ship it; derive
    # it here from the freshly generated owner URL and kip_backup password.
    _append_application_role_credentials(root, target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
