"""Shared fixtures, and the pin that makes this suite run the same everywhere.

`tests/environment.py` explains why: 3.13.0 and 3.14.0 were both tagged on a
green local gate and both failed CI, because a test read configuration or
terminal settings from the shell that launched pytest instead of pinning them.

Everything in this module is imported before any test module, so the terminal
pin lands before Typer caches its colour switches, and the recorder is in
place before anything reads `os.environ`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.environment import (
    REPOSITORY_ROOT,
    TEST_DATABASE_SKIP_REASON,
    TEST_POSTGRES_URL,
    ambient_read_violations,
    apply_terminal_pin,
    ci_environment,
    half_pinned_groups,
    install_recording_environ,
    stray_kip_keys,
)

# Import-time, in this order: Typer reads the colour switches once, when it is
# imported by the first test module that touches the CLI.
apply_terminal_pin()
_RECORDER = install_recording_environ()

from kip.adapters.repository.memory import MemoryRepository  # noqa: E402
from kip.container import build_container  # noqa: E402
from kip.settings import Settings  # noqa: E402


@pytest.fixture(scope="session")
def _pinned_cas_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Content store for the pinned profile.

    CI points KIP_CAS_PATH at `var/ci-cas` inside the workspace. A local run
    must not write into the checkout or into a deployment's store, so the pin
    uses a temporary directory instead; what matters for parity is that the
    variable is always set and never inherited.
    """
    return tmp_path_factory.mktemp("pinned-cas")


@pytest.fixture(autouse=True)
def pinned_environment(_pinned_cas_root: Path) -> Iterator[None]:
    """Start every test from CI's environment and fail it for reaching outside.

    On the way in: apply the profile, dropping every KIP_* variable CI does
    not export and every terminal switch that could change how Rich renders.

    On the way out, three checks:

    1. the test did not leave the process environment changed for the next one;
    2. the test did not pin one half of a coupled pair and inherit the other
       (3.13.0 set KIP_PROJECT_ROOT and inherited KIP_CONFIG);
    3. the test did not read a variable that the developer's shell exports and
       CI does not.
    """
    before = dict(os.environ)
    patch = pytest.MonkeyPatch()
    for key, value in ci_environment(_pinned_cas_root).items():
        if value is None:
            patch.delenv(key, raising=False)
        else:
            patch.setenv(key, value)
    for key in stray_kip_keys(os.environ):
        patch.delenv(key, raising=False)

    _RECORDER.start_recording()
    try:
        yield
    finally:
        reads, writes = _RECORDER.stop_recording()
        patch.undo()

    leaked = sorted(
        key
        for key in set(before) | set(os.environ)
        if key != "PYTEST_CURRENT_TEST" and before.get(key) != os.environ.get(key)
    )
    if leaked:
        pytest.fail(
            "this test changed the process environment and did not put it back: "
            + ", ".join(leaked)
            + ". Use monkeypatch.setenv/delenv so the next test starts from the "
            "pinned profile."
        )

    half_pinned = half_pinned_groups(reads, writes)
    if half_pinned:
        pytest.fail(
            "this test pinned part of the environment it depends on and "
            "inherited the rest:\n  - "
            + "\n  - ".join(half_pinned)
            + "\nSet every variable in the group, the way "
            "tests/test_config_key_validation.py names its own KIP_CONFIG."
        )

    ambient = ambient_read_violations(reads, writes)
    if ambient:
        pytest.fail(
            "this test read environment variables that your shell exports and "
            "CI does not: "
            + ", ".join(ambient)
            + ". Pin them in tests/environment.py:CI_KIP_ENVIRONMENT, set them "
            "in the test, or add them to KNOWN_ABSENT_AMBIENT_KEYS if they must "
            "stay unset."
        )


@pytest.fixture()
def repository_config(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Opt in to the repository's own shipped configuration, by absolute path.

    The pinned profile keeps CI's relative `config/kip.example.toml`, which
    resolves against the current working directory. A test that changes the
    working directory or the project root and still wants the shipped config
    asks for it here instead of hoping the relative path lands somewhere.
    """
    path = REPOSITORY_ROOT / "config/kip.example.toml"
    monkeypatch.setenv("KIP_CONFIG", str(path))
    monkeypatch.setenv("KIP_PROJECT_ROOT", str(REPOSITORY_ROOT))
    return path


@pytest.fixture()
def postgres_database_url(monkeypatch: pytest.MonkeyPatch) -> str:
    """Opt in to the test-designated database, `KIP_TEST_POSTGRES_URL`.

    The pinned profile sets `KIP_DATABASE_URL=memory://` so no test reaches a
    real database by accident. A test that needs one asks for it here and is
    skipped when the run names none. It never falls back to the ambient
    `KIP_DATABASE_URL`, which on a developer machine is the live deployment;
    integration modules import the same `TEST_POSTGRES_URL` constant.
    """
    if not TEST_POSTGRES_URL:
        pytest.skip(TEST_DATABASE_SKIP_REASON)
    monkeypatch.setenv("KIP_DATABASE_URL", TEST_POSTGRES_URL)
    return TEST_POSTGRES_URL


@pytest.fixture()
def test_container(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "app": {"workspace": "default"},
            "search": {"semantic_enabled": False, "korean_ngram_min": 2, "korean_ngram_max": 4},
            "graph": {"backend": "memory"},
            "sources": {
                "filesystem": [
                    {
                        "name": "fixture",
                        "root": str(source_root),
                        "enabled": True,
                        "read_only": True,
                        "settle_seconds": 0,
                        "include_extensions": [".txt", ".md", ".xlsx", ".pdf", ".hwp", ".hwpx", ".docx"],
                        "exclude_globs": [],
                        "acl_scope": "workspace:default",
                    }
                ]
            },
            "parsers": {"hwp": {"order": ["paired_pdf"]}},
        },
        environment="test",
        workspace="default",
        database_url="memory://",
        cas_path=tmp_path / "cas",
        api_key="test-key",
        admin_key="test-admin",
    )
    return build_container(settings, repository=MemoryRepository())
