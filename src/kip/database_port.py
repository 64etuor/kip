"""Refuse a loopback database URL that names another deployment's port.

The bash guard in `scripts/common.sh` (`kip_database_port_check`) is the
fast preflight for `./scripts/kip`. This module is the same rule for the
Python entrypoints (`.venv/bin/kip`, `python -m kip.cli`, `kip-mcp`) and
for `kip doctor`, including `KIP_DATABASE_URL_FILE` when the env var is empty.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_URL_FILES = {
    "KIP_DATABASE_URL": "KIP_DATABASE_URL_FILE",
    "KIP_BACKUP_DATABASE_URL": "KIP_BACKUP_DATABASE_URL_FILE",
}
_MISMATCH_FIX = (
    "set the same port in KIP_DATABASE_URL and KIP_BACKUP_DATABASE_URL in .env, "
    "or KIP_DATABASE_PORT_CHECK=off for a deliberately separate local PostgreSQL"
)
_BASH_OVERRIDE = (
    "If this URL deliberately names a separate local PostgreSQL, set "
    "KIP_DATABASE_PORT_CHECK=off."
)


@dataclass(frozen=True, slots=True)
class DatabasePortVerdict:
    ok: bool
    required: bool
    skipped: str | None
    reason: str | None
    fix: str | None

    def doctor_check(self) -> dict[str, object]:
        details: dict[str, object] = {"reason": self.reason}
        if self.skipped is not None:
            details["skipped"] = self.skipped
        if self.fix is not None:
            details["fix"] = self.fix
        return {
            "name": "database_url_port",
            "ok": self.ok,
            "required": self.required,
            "details": details,
        }

    def refuse_message(self) -> str:
        if self.reason is None:
            return ""
        if self.reason.startswith("KIP_POSTGRES_PORT="):
            return f"error: {self.reason}\n"
        if self.fix is not None and "invalid port" in self.reason:
            return f"error: {self.reason}. {self.fix[0].upper() + self.fix[1:]}. {_BASH_OVERRIDE}\n"
        return (
            f"error: {self.reason}. Set the same port in KIP_DATABASE_URL and "
            f"KIP_BACKUP_DATABASE_URL in .env. {_BASH_OVERRIDE}\n"
        )


def project_root_from_env() -> Path:
    configured = os.environ.get("KIP_PROJECT_ROOT")
    return Path(configured).resolve() if configured else Path.cwd().resolve()


def _url_from_env(name: str) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    path = os.environ.get(_URL_FILES[name], "")
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def evaluate_database_port(project_root: Path) -> DatabasePortVerdict:
    """Same host/port/skip rules as `kip_database_port_check`, reported not refused."""
    published_text = os.environ.get("KIP_POSTGRES_PORT", "")
    override = os.environ.get("KIP_DATABASE_PORT_CHECK") == "off"
    generated = (project_root / "compose.generated.yaml").exists() or (
        project_root / "config/kip.generated.toml"
    ).exists()
    if override or generated or not published_text:
        skipped = (
            "KIP_DATABASE_PORT_CHECK=off" if override
            else "generated deployment (setup verify checks it)" if generated
            else "KIP_POSTGRES_PORT is not set"
        )
        return DatabasePortVerdict(
            ok=True, required=False, skipped=skipped, reason=None, fix=None,
        )
    if not (published_text.isascii() and published_text.isdigit() and len(published_text) <= 5):
        return DatabasePortVerdict(
            ok=False,
            required=True,
            skipped=None,
            reason=f"KIP_POSTGRES_PORT={published_text!r} is not a port number",
            fix=None,
        )
    published = int(published_text)
    for name in _URL_FILES:
        value = _url_from_env(name)
        if "://" not in value:
            continue
        try:
            parts = urlsplit(value)
            host = (parts.hostname or "").lower()
        except ValueError:
            continue
        try:
            # `is None`, not `or`: an explicit port 0 is reported, never defaulted.
            port = 5432 if parts.port is None else parts.port
        except ValueError:
            location = parts.netloc.rpartition("@")[2]
            raw = (location.rpartition("]")[2] if location.startswith("[") else location).partition(":")[2]
            return DatabasePortVerdict(
                ok=False,
                required=True,
                skipped=None,
                reason=f"{name} has an invalid port ({raw!r}), so it cannot connect",
                fix=f"set a port number from 1 to 65535 in {name} in .env",
            )
        if host in _LOOPBACK and port != published:
            return DatabasePortVerdict(
                ok=False,
                required=True,
                skipped=None,
                reason=(
                    f"{name} uses port {port}, but this deployment publishes PostgreSQL on "
                    f"{published} (KIP_POSTGRES_PORT); another deployment may own port {port}"
                ),
                fix=_MISMATCH_FIX,
            )
    return DatabasePortVerdict(ok=True, required=True, skipped=None, reason=None, fix=None)


def database_port_doctor_check(project_root: Path) -> dict[str, object]:
    return evaluate_database_port(project_root).doctor_check()


def refuse_mismatched_database_port(project_root: Path | None = None) -> None:
    """Exit 2 with a bash-shaped stderr line when the guard applies and fails.

    No-op when the check is skipped (unset published port, generated
    deployment, override) or the URLs match. stdout is left empty so an MCP
    protocol stream is not corrupted.
    """
    verdict = evaluate_database_port(project_root or project_root_from_env())
    if verdict.ok or not verdict.required:
        return
    sys.stderr.write(verdict.refuse_message())
    raise SystemExit(2)
