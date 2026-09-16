"""The environment this suite pins, and the guard that keeps tests inside it.

Two releases were tagged on a green local gate and failed CI the same day, for
the same reason: a test inherited ambient state instead of pinning it.

* **3.13.0** - four tests wrote a config under ``tmp_path`` and set
  ``KIP_PROJECT_ROOT``, but CI exports ``KIP_CONFIG`` for the whole job, so
  ``Settings.load()`` read the repository's own configuration instead of the
  file under test. They passed locally only because the developer's ``.env``
  pointed ``KIP_CONFIG`` at a relative path that happened to resolve inside
  the temporary root.
* **3.14.0** - three tests matched phrases in ``--help`` output. CI renders
  help on an 80 column terminal with colour forced on, which puts escape
  sequences inside words and wraps sentences mid-phrase.

`ci_environment()` is therefore the single environment every test starts
from, in CI and on a developer machine alike. A test that needs different
values sets them itself; nothing is inherited from the shell that launched
pytest.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from urllib.parse import unquote, urlsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# The shell that launched pytest, captured before anything is pinned. The
# guard compares against it to tell "the developer exports this" apart from
# "nobody does", and the opt-in fixtures hand back the few values a test is
# allowed to take from it.
AMBIENT_ENVIRONMENT: Mapping[str, str] = MappingProxyType(dict(os.environ))

# Terminal rendering, pinned to what a GitHub Actions runner gives the CLI.
#
# The runner is not a TTY and exports no TERM, so Rich falls back to 80
# columns; Typer's `rich_utils` reads GITHUB_ACTIONS at import time and forces
# colour on, which is why help text arrives with escape sequences inside
# words. `FORCE_COLOR` is the portable spelling of that same switch, and this
# module sets it before any test module imports Typer. Anything that could
# turn colour back off or change the width is removed rather than left to the
# shell.
CI_TERMINAL_ENVIRONMENT: Mapping[str, str | None] = MappingProxyType(
    {
        "COLUMNS": "80",
        "LINES": "24",
        "TERM": None,
        "FORCE_COLOR": "1",
        "NO_COLOR": None,
        "PY_COLORS": None,
        "CLICOLOR": None,
        "CLICOLOR_FORCE": None,
        "TERMINAL_WIDTH": None,
        "_TYPER_FORCE_DISABLE_TERMINAL": None,
    }
)

# What the `quality` job of .github/workflows/ci.yml exports, minus the two
# values a local run cannot reproduce:
#
# * KIP_DATABASE_URL is pinned to `memory://` instead of the CI service
#   database. A real database is an explicit opt-in through
#   `KIP_TEST_POSTGRES_URL` only (`TEST_POSTGRES_URL` below), never something
#   an ordinary unit test reaches by accident.
# * KIP_CAS_PATH is pinned to a per-session temporary directory instead of
#   `var/ci-cas`, so a test run never writes into a checkout or a deployment.
#
# KIP_CONFIG keeps CI's relative spelling on purpose: a test that repoints
# KIP_PROJECT_ROOT and does not also name its own config file resolves a file
# that is not there, which is exactly what CI did to 3.13.0.
# `tests/test_environment_parity.py` fails if CI's exports drift from this.
CI_KIP_ENVIRONMENT: Mapping[str, str] = MappingProxyType(
    {
        "KIP_CONFIG": "config/kip.example.toml",
        "KIP_WORKSPACE": "default",
        "KIP_ENV": "test",
    }
)

# CI exports these two as well; the pin replaces their values, for the reasons
# above, and `tests/test_environment_parity.py` holds the substitution to
# exactly this list.
CI_SUBSTITUTED_KIP_KEYS: frozenset[str] = frozenset({"KIP_DATABASE_URL", "KIP_CAS_PATH"})

# CI does not export KIP_PROJECT_ROOT: it runs pytest from the checkout, so
# `Settings.load()` falls back to the working directory and lands there
# anyway. Pinning it to the same directory keeps a test that changes the
# working directory from quietly resolving a different tree.
LOCAL_ONLY_KIP_KEYS: frozenset[str] = frozenset({"KIP_PROJECT_ROOT"})

# Every other KIP_* variable is deleted. CI exports none of them; a developer
# shell that sourced `.env` exports a dozen, including an API key, an ACL
# scope list and a live database URL.
CI_DYNAMIC_KIP_KEYS: frozenset[str] = CI_SUBSTITUTED_KIP_KEYS | LOCAL_ONLY_KIP_KEYS

_TERMINAL_KEYS: frozenset[str] = frozenset(CI_TERMINAL_ENVIRONMENT)

# Variables whose values are resolved together. A test that pins one of a
# group and then reads another from the profile has half-pinned its
# environment, which is precisely the 3.13.0 defect: it set KIP_PROJECT_ROOT,
# wrote `config/kip.toml` under it, and let KIP_CONFIG come from elsewhere.
#
# Only variables that are read *independently* belong here. `KIP_DATABASE_URL`
# and `KIP_DATABASE_URL_FILE` do not: `Settings` reads both on every load and
# refuses to start when both are set, so naming one already settles the pair.
COUPLED_KEY_GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    ("configuration resolution", frozenset({"KIP_CONFIG", "KIP_PROJECT_ROOT"})),
)

# Why a variable a developer machine may export is left unset for every test.
_SETTINGS_OVERRIDE = (
    "Settings.load(), the container or the CLI read it on every load; CI exports "
    "none, so config/kip.example.toml's default applies"
)
_SECRET_FILE_TWIN = (
    "the *_FILE twin of a secret Settings resolves; a deployment may export it "
    "instead of the plain variable, and CI exports neither"
)
_REQUEST_IDENTITY = (
    "the identity a CLI or MCP request carries; .env.example exports it (empty), "
    "and a test that needs roles passes them itself"
)
_INSTALL_CHOICE = (
    "an install-time choice bootstrap records in .env (KIP_SEMANTIC=off); the "
    "setup planner reads it, and a plan under test must not depend on how this "
    "machine was installed"
)
_EXTERNAL_CREDENTIAL = (
    "a connector or model-provider credential; a test that exercises the adapter "
    "sets its own, and no test may reach the real service"
)
_DEPLOYMENT_ONLY = (
    "consumed by scripts/, Compose or the model runtime, or only named in text "
    "the code renders; a test of that path passes its own value"
)

# Every KIP_* variable the code under src/ names, .env.example exports or
# bootstrap can append to .env, and that the pinned profile does not set,
# with the reason it must read as absent. The pin deletes them all, so a read
# resolves to "absent" in CI and locally alike. A read of any *other* ambient
# variable fails the test that made it. `tests/test_environment_parity.py`
# collects the names from those files and fails when one is neither pinned nor
# listed here, which is what let KIP_ROLES from .env.example break the local
# gate while CI stayed green.
KNOWN_ABSENT_AMBIENT_KEYS: Mapping[str, str] = MappingProxyType(
    {
        "KIP_ACL_SCOPES": _SETTINGS_OVERRIDE,
        "KIP_ADMIN_KEY": _SETTINGS_OVERRIDE,
        "KIP_API_ACL_SCOPES": _SETTINGS_OVERRIDE,
        "KIP_API_HOST": _SETTINGS_OVERRIDE,
        "KIP_API_KEY": _SETTINGS_OVERRIDE,
        "KIP_API_PORT": _SETTINGS_OVERRIDE,
        "KIP_API_PRINCIPAL_ID": _SETTINGS_OVERRIDE,
        "KIP_DATABASE_POOL_MAX_SIZE": _SETTINGS_OVERRIDE,
        "KIP_DATABASE_STATEMENT_TIMEOUT_MS": _SETTINGS_OVERRIDE,
        "KIP_IDENTITY_MODE": _SETTINGS_OVERRIDE,
        "KIP_JWT_AUDIENCE": _SETTINGS_OVERRIDE,
        "KIP_JWT_ISSUER": _SETTINGS_OVERRIDE,
        "KIP_JWT_JWKS_URL": _SETTINGS_OVERRIDE,
        "KIP_LOG_LEVEL": _SETTINGS_OVERRIDE,
        "KIP_MAX_REQUEST_BYTES": _SETTINGS_OVERRIDE,
        "KIP_PRINCIPAL_ID": _SETTINGS_OVERRIDE,
        "KIP_ADMIN_KEY_FILE": _SECRET_FILE_TWIN,
        "KIP_API_KEY_FILE": _SECRET_FILE_TWIN,
        "KIP_DATABASE_URL_FILE": _SECRET_FILE_TWIN,
        "KIP_BACKUP_DATABASE_URL_FILE": _SECRET_FILE_TWIN,
        "KIP_GENERATION_API_KEY_FILE": _SECRET_FILE_TWIN,
        "KIP_OPENAI_API_KEY_FILE": _SECRET_FILE_TWIN,
        "KIP_ROLES": _REQUEST_IDENTITY,
        # Setup verify reads it from an MCP registration's `env` mapping, never
        # from the process environment; the installer's shell profile exports it
        # only for the `kip` launcher.
        "KIP_HOME": "launcher override read from a registration entry, not the environment",
        "KIP_SEMANTIC": _INSTALL_CHOICE,
        "KIP_SEMANTIC_RERANKER": _INSTALL_CHOICE,
        "KIP_ANTHROPIC_API_KEY": _EXTERNAL_CREDENTIAL,
        "KIP_GENERATION_API_KEY": _EXTERNAL_CREDENTIAL,
        "KIP_IMAP_PASSWORD": _EXTERNAL_CREDENTIAL,
        "KIP_IMAP_USERNAME": _EXTERNAL_CREDENTIAL,
        "KIP_OPENAI_API_KEY": _EXTERNAL_CREDENTIAL,
        "KIP_SLACK_BOT_TOKEN": _EXTERNAL_CREDENTIAL,
        "KIP_API_DB_PASSWORD": _DEPLOYMENT_ONLY,
        "KIP_BACKUP_DATABASE_URL": _DEPLOYMENT_ONLY,
        "KIP_BACKUP_DB_PASSWORD": _DEPLOYMENT_ONLY,
        "KIP_BACKUP_PATH": _DEPLOYMENT_ONLY,
        "KIP_COMPOSE_ADOPT": (
            "an operator's one-time override of the Compose project guard in "
            "scripts/common.sh; setup verify only names it in a fix message"
        ),
        "KIP_DATABASE_PORT_CHECK": (
            "an operator's override of the database port guard in scripts/common.sh "
            "and the Python CLI/MCP entrypoints; kip doctor's database_url_port check "
            "reads it, and a test of that check sets its own value"
        ),
        "KIP_CONTAINER_DATABASE_URL": _DEPLOYMENT_ONLY,
        "KIP_EMBEDDING_DIMENSIONS": _DEPLOYMENT_ONLY,
        "KIP_EMBEDDING_MODEL": _DEPLOYMENT_ONLY,
        "KIP_EMBEDDING_REVISION": _DEPLOYMENT_ONLY,
        "KIP_EMBEDDING_SERVED_MODEL": _DEPLOYMENT_ONLY,
        "KIP_EMBEDDING_SERVER_BATCH_SIZE": _DEPLOYMENT_ONLY,
        "KIP_MODELS_IMAGE": _DEPLOYMENT_ONLY,
        "KIP_NAS_PATH": _DEPLOYMENT_ONLY,
        "KIP_ONTOLOGY_PATH": _DEPLOYMENT_ONLY,
        "KIP_POSTGRES_IMAGE": _DEPLOYMENT_ONLY,
        "KIP_POSTGRES_PORT": _DEPLOYMENT_ONLY,
        "KIP_PYTHON": _DEPLOYMENT_ONLY,
        "KIP_RERANKER_MODEL": _DEPLOYMENT_ONLY,
        "KIP_RERANKER_REVISION": _DEPLOYMENT_ONLY,
        "KIP_RERANKER_SERVED_MODEL": _DEPLOYMENT_ONLY,
        "KIP_RERANKER_SERVER_BATCH_SIZE": _DEPLOYMENT_ONLY,
        "KIP_SEMANTIC_HOST": _DEPLOYMENT_ONLY,
        "KIP_SEMANTIC_PORT": _DEPLOYMENT_ONLY,
        "KIP_WORKER_DB_PASSWORD": _DEPLOYMENT_ONLY,
    }
)

# The only real PostgreSQL database a test may use. It is never
# KIP_DATABASE_URL: on a developer machine `.env` points that at the live
# deployment, and a test that creates workspaces, roles or migrations there
# writes into production data. Absent means every real-database test skips.
TEST_DATABASE_KEY = "KIP_TEST_POSTGRES_URL"
TEST_DATABASE_SKIP_REASON = (
    f"{TEST_DATABASE_KEY} is not set; point it at a throwaway PostgreSQL database "
    "to run the real-database tests"
)


def _database_identity(url: str) -> tuple[str, int, str]:
    parts = urlsplit(url)
    host = (parts.hostname or "localhost").lower()
    if host in {"127.0.0.1", "::1", "localhost"}:
        host = "localhost"
    return host, parts.port or 5432, unquote(parts.path.lstrip("/")) or (parts.username or "")


def resolve_test_database_url(environment: Mapping[str, str]) -> str | None:
    """``KIP_TEST_POSTGRES_URL`` from ``environment``, refused if it is the deployment's.

    A URL naming the same server and database as ``KIP_DATABASE_URL`` is the
    deployment database under another name, and raises. The one exception is
    a GitHub Actions runner, whose ``KIP_DATABASE_URL`` is a service container
    created for the job and discarded with it.
    """
    url = environment.get(TEST_DATABASE_KEY) or None
    deployment = environment.get("KIP_DATABASE_URL", "")
    secret_file = environment.get("KIP_DATABASE_URL_FILE", "")
    if not deployment and secret_file:
        # A deployment may keep its URL in a secret file rather than the
        # variable; it is the same database and must be refused the same way.
        try:
            deployment = Path(secret_file).read_text(encoding="utf-8").strip()
        except OSError:
            deployment = ""
    if (
        url
        and deployment.startswith(("postgresql://", "postgres://"))
        and environment.get("GITHUB_ACTIONS") != "true"
        and _database_identity(url) == _database_identity(deployment)
    ):
        raise ValueError(
            f"{TEST_DATABASE_KEY} names the same database as KIP_DATABASE_URL "
            f"({'/'.join(map(str, _database_identity(url)))}). Tests create and delete "
            "workspaces, roles and migrations there; point it at a throwaway database."
        )
    return url


# Resolved once, from the shell that launched pytest, before the pin removes
# the variable. Integration modules import it instead of reading os.environ.
TEST_POSTGRES_URL: str | None = resolve_test_database_url(AMBIENT_ENVIRONMENT)


def is_environment_sensitive(key: str) -> bool:
    """True for the variables that decide what the code under test resolves."""
    return key.startswith("KIP_") or key in _TERMINAL_KEYS


def ci_environment(cas_path: Path) -> dict[str, str | None]:
    """The full pinned profile. ``None`` means "remove this variable"."""
    profile: dict[str, str | None] = dict(CI_TERMINAL_ENVIRONMENT)
    profile.update(CI_KIP_ENVIRONMENT)
    profile["KIP_DATABASE_URL"] = "memory://"
    profile["KIP_CAS_PATH"] = str(cas_path)
    profile["KIP_PROJECT_ROOT"] = str(REPOSITORY_ROOT)
    return profile


def stray_kip_keys(environment: Mapping[str, str]) -> list[str]:
    """KIP_* variables present that the pinned profile does not define."""
    pinned = set(CI_KIP_ENVIRONMENT) | CI_DYNAMIC_KIP_KEYS
    return sorted(key for key in environment if key.startswith("KIP_") and key not in pinned)


def apply_terminal_pin() -> None:
    """Pin terminal rendering in the live process environment.

    Called at ``tests/conftest.py`` import time, before any test module
    imports Typer, because ``typer.rich_utils`` reads the colour switches once
    at import and caches them in module constants.
    """
    for key, value in CI_TERMINAL_ENVIRONMENT.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class RecordingEnviron(os._Environ):  # type: ignore[misc]
    """``os.environ`` that can report which variables a test read and wrote.

    Installed once, in place of ``os.environ``, over the same backing dict, so
    ``os.getenv``, ``subprocess`` and every ``os.environ`` reader in ``src/``
    go through it. Recording is off until a test starts, so the profile the
    fixture itself applies is not mistaken for something the test chose.
    """

    __slots__ = ("_reads", "_writes")

    def __init__(self, base: os._Environ) -> None:  # type: ignore[type-arg]
        super().__init__(
            base._data,
            base.encodekey,
            base.decodekey,
            base.encodevalue,
            base.decodevalue,
        )
        self._reads: set[str] | None = None
        self._writes: set[str] | None = None

    # -- recording control -------------------------------------------------
    def start_recording(self) -> None:
        self._reads = set()
        self._writes = set()

    def stop_recording(self) -> tuple[frozenset[str], frozenset[str]]:
        reads = frozenset(self._reads or ())
        writes = frozenset(self._writes or ())
        self._reads = None
        self._writes = None
        return reads, writes

    # -- recorded access ---------------------------------------------------
    def __getitem__(self, key: str) -> str:
        if self._reads is not None and isinstance(key, str) and is_environment_sensitive(key):
            self._reads.add(key)
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: str) -> None:
        if self._writes is not None and isinstance(key, str) and is_environment_sensitive(key):
            self._writes.add(key)
        super().__setitem__(key, value)

    def __delitem__(self, key: str) -> None:
        if self._writes is not None and isinstance(key, str) and is_environment_sensitive(key):
            self._writes.add(key)
        super().__delitem__(key)


def install_recording_environ() -> RecordingEnviron:
    """Swap ``os.environ`` for a recorder, or return the one already there."""
    if isinstance(os.environ, RecordingEnviron):
        return os.environ
    recorder = RecordingEnviron(os.environ)
    # Deliberate: the recorder shares `os.environ._data`, so the process
    # environment is the same object seen through an instrumented view.
    os.environ = recorder  # type: ignore[assignment]  # noqa: B003
    return recorder


def ambient_read_violations(reads: frozenset[str], writes: frozenset[str]) -> list[str]:
    """Reads of variables the developer's shell exports and CI does not.

    The pin deletes them, so the read returns "absent" in both environments
    and the test still passes; the point is that a *new* one is a dependency
    on whoever ran the suite, and it is reported before it reaches a tag.
    """
    pinned = set(CI_KIP_ENVIRONMENT) | CI_DYNAMIC_KIP_KEYS | _TERMINAL_KEYS
    return sorted(
        key
        for key in reads - writes - pinned - KNOWN_ABSENT_AMBIENT_KEYS.keys()
        if key in AMBIENT_ENVIRONMENT
    )


def half_pinned_groups(reads: frozenset[str], writes: frozenset[str]) -> list[str]:
    """Groups where the test pinned one variable and inherited a coupled one."""
    findings = []
    for label, group in COUPLED_KEY_GROUPS:
        pinned_here = writes & group
        inherited = (reads & group) - writes
        if pinned_here and inherited:
            findings.append(
                f"{label}: the test set {', '.join(sorted(pinned_here))} "
                f"but read {', '.join(sorted(inherited))} from the pinned profile"
            )
    return findings


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BOX_DRAWING = re.compile(r"[\u2500-\u257f]")


def plain_terminal_text(text: str) -> str:
    """Rendered terminal output with colour, box drawing and wrapping removed.

    Any assertion about CLI help or a Rich-rendered message must go through
    this. The 3.14.0 tests asserted on the raw string and broke the moment the
    terminal was 80 columns wide with colour on.
    """
    return _BOX_DRAWING.sub(" ", _ANSI.sub("", text)).replace("\xa0", " ").strip()


def collapse_whitespace(text: str) -> str:
    """`plain_terminal_text` with line wrapping collapsed to single spaces."""
    return re.sub(r"\s+", " ", plain_terminal_text(text))
