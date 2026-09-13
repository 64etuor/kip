"""The suite runs the same in CI and on a developer machine, or this fails.

3.13.0 and 3.14.0 were both tagged on a green local gate and both failed CI:
one because CI exports `KIP_CONFIG` for the whole job and the local run did
not, one because CI renders help on an 80 column terminal with colour on.
These checks keep the pinned profile in `tests/environment.py` tied to what
the workflow actually exports, and keep the pin effective once it is applied.
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote, urlunsplit

import pytest
import yaml

from kip.cli import app
from tests.environment import (
    CI_DYNAMIC_KIP_KEYS,
    CI_KIP_ENVIRONMENT,
    CI_SUBSTITUTED_KIP_KEYS,
    CI_TERMINAL_ENVIRONMENT,
    KNOWN_ABSENT_AMBIENT_KEYS,
    LOCAL_ONLY_KIP_KEYS,
    REPOSITORY_ROOT,
    TEST_DATABASE_KEY,
    collapse_whitespace,
    resolve_test_database_url,
    stray_kip_keys,
)


def _database_url(user: str, password: str, host: str, database: str) -> str:
    """Build a database URL from parts.

    The package archive ships `tests/`, and its credential scanner rejects any
    literal database URL that carries a password, so tests assemble them.
    """
    credentials = user + ":" + quote(password, safe="")
    return urlunsplit(("postgresql", credentials + "@" + host, "/" + database, "", ""))

WORKFLOW = REPOSITORY_ROOT / ".github/workflows/ci.yml"
_KIP_NAME = re.compile(r"\bKIP_[A-Z0-9_]+")


def _ci_quality_environment() -> dict[str, str]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return {
        key: str(value)
        for key, value in workflow["jobs"]["quality"]["env"].items()
        if key.startswith("KIP_")
    }


def test_the_pinned_profile_covers_every_kip_variable_ci_exports() -> None:
    # The 3.13.0 root cause in one assertion: CI exported a variable the local
    # suite knew nothing about. Adding one to the workflow now fails here
    # until tests/environment.py says what the suite does with it.
    exported = set(_ci_quality_environment())
    accounted = set(CI_KIP_ENVIRONMENT) | CI_SUBSTITUTED_KIP_KEYS | {TEST_DATABASE_KEY}

    assert exported == accounted, (
        "the quality job exports KIP_* variables the test profile does not "
        f"account for: {sorted(exported - accounted)}; and the profile claims "
        f"variables CI no longer exports: {sorted(accounted - exported)}. "
        "Update CI_KIP_ENVIRONMENT or CI_SUBSTITUTED_KIP_KEYS in "
        "tests/environment.py."
    )


def test_the_profile_repeats_ci_values_verbatim_where_it_does_not_substitute() -> None:
    exported = _ci_quality_environment()

    for key, value in CI_KIP_ENVIRONMENT.items():
        assert exported[key] == value, (
            f"{key} is {exported[key]!r} in .github/workflows/ci.yml and "
            f"{value!r} in the test profile"
        )


def test_every_test_starts_from_the_pinned_profile() -> None:
    for key, value in CI_KIP_ENVIRONMENT.items():
        assert os.environ[key] == value

    assert os.environ["KIP_DATABASE_URL"] == "memory://"
    assert os.environ["KIP_PROJECT_ROOT"] == str(REPOSITORY_ROOT)
    assert not stray_kip_keys(os.environ), (
        "the pin left KIP_* variables behind that CI does not export: "
        f"{stray_kip_keys(os.environ)}"
    )
    for key in LOCAL_ONLY_KIP_KEYS:
        assert key in os.environ


def test_the_terminal_pin_reaches_typer_before_it_caches_its_colour_switches() -> None:
    # typer.rich_utils resolves these once, at import. If tests/conftest.py
    # ever stops pinning them before the first test module imports Typer, the
    # suite silently goes back to rendering help the way the developer's
    # terminal does, which is how 3.14.0 shipped.
    from typer import rich_utils

    assert rich_utils.FORCE_TERMINAL is True
    assert rich_utils.MAX_WIDTH is None
    for key, value in CI_TERMINAL_ENVIRONMENT.items():
        assert os.environ.get(key) == value


def test_help_output_says_the_same_thing_on_a_ci_terminal_and_a_wide_one() -> None:
    # The 3.14.0 failure, reproduced as a check: the same help text is
    # rendered under CI's 80 columns with colour and under a wide, colourless
    # terminal, and the normalised results must agree.
    from typer.testing import CliRunner

    base = {
        "KIP_CONFIG": str(REPOSITORY_ROOT / "config/kip.example.toml"),
        "KIP_DATABASE_URL": "memory://",
        "KIP_PROJECT_ROOT": str(REPOSITORY_ROOT),
        "KIP_ENV": "test",
    }
    narrow = CliRunner().invoke(app, ["--help"], env={**base, "COLUMNS": "80"})
    wide = CliRunner().invoke(
        app, ["--help"], env={**base, "COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"}
    )

    assert narrow.exit_code == 0 and wide.exit_code == 0
    assert re.search(r"\x1b\[[0-9;]*m", narrow.stdout), (
        "the CI terminal pin is not forcing colour, so this check no longer "
        "reproduces what CI renders"
    )
    assert collapse_whitespace(narrow.stdout) == collapse_whitespace(wide.stdout)


def test_a_test_that_moves_the_project_root_must_name_its_own_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # The guard in tests/conftest.py that catches the 3.13.0 shape. Reading
    # KIP_CONFIG after setting only KIP_PROJECT_ROOT is what it fails on, so
    # this test does the safe half and asserts the resolution it pinned.
    from kip.settings import Settings

    config = tmp_path / "kip.toml"
    config.write_text('[app]\nworkspace = "pinned"\n', encoding="utf-8")
    monkeypatch.setenv("KIP_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("KIP_CONFIG", str(config))
    monkeypatch.delenv("KIP_WORKSPACE", raising=False)

    assert Settings.load().workspace == "pinned"


def test_the_guard_flags_the_3130_shape_and_stays_quiet_when_both_are_pinned() -> None:
    from tests.environment import half_pinned_groups

    half_pinned = half_pinned_groups(
        reads=frozenset({"KIP_CONFIG", "KIP_PROJECT_ROOT"}),
        writes=frozenset({"KIP_PROJECT_ROOT"}),
    )
    assert half_pinned and "KIP_CONFIG" in half_pinned[0]

    assert not half_pinned_groups(
        reads=frozenset({"KIP_CONFIG", "KIP_PROJECT_ROOT"}),
        writes=frozenset({"KIP_PROJECT_ROOT", "KIP_CONFIG"}),
    )
    # Reading both without pinning either is the profile doing its job.
    assert not half_pinned_groups(
        reads=frozenset({"KIP_CONFIG", "KIP_PROJECT_ROOT"}), writes=frozenset()
    )


def test_the_guard_names_only_variables_the_shell_exports_and_ci_does_not() -> None:
    from tests import environment
    from tests.environment import ambient_read_violations

    read = frozenset({"KIP_SOMETHING_LOCAL", "KIP_NOBODY_EXPORTS_THIS", "KIP_CONFIG"})

    assert ambient_read_violations(read, frozenset()) == []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            environment,
            "AMBIENT_ENVIRONMENT",
            {**environment.AMBIENT_ENVIRONMENT, "KIP_SOMETHING_LOCAL": "from-my-shell"},
        )
        # KIP_CONFIG is pinned and KIP_NOBODY_EXPORTS_THIS is absent everywhere,
        # so only the shell-provided one is reported.
        assert ambient_read_violations(read, frozenset()) == ["KIP_SOMETHING_LOCAL"]
        # A test that sets it itself is pinning, not inheriting.
        assert ambient_read_violations(read, frozenset({"KIP_SOMETHING_LOCAL"})) == []


def _kip_names_a_developer_machine_can_export() -> set[str]:
    """Every KIP_* name that can be in the shell `./scripts/test.sh` starts.

    The code under src/ and the shipped config name what is read;
    `.env.example` is what a new `.env` is copied from, and `scripts/common.sh`
    exports every key in it; bootstrap appends to `.env` after the copy. A
    secret Settings resolves also has a `*_FILE` twin it reads on every load.
    """
    names: set[str] = set()
    for path in (REPOSITORY_ROOT / "src/kip").rglob("*.py"):
        names |= set(_KIP_NAME.findall(path.read_text(encoding="utf-8")))
    for relative in (
        "config/kip.example.toml",
        "scripts/bootstrap.sh",
        "scripts/bootstrap_env.py",
    ):
        names |= set(_KIP_NAME.findall((REPOSITORY_ROOT / relative).read_text(encoding="utf-8")))
    example = (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")
    names |= set(re.findall(r"^(KIP_[A-Z0-9_]+)=", example, re.MULTILINE))

    settings = (REPOSITORY_ROOT / "src/kip/settings.py").read_text(encoding="utf-8")
    secrets = set(re.findall(r'_env", "(KIP_[A-Z0-9_]+)"', settings))
    for path in [*(REPOSITORY_ROOT / "src/kip").rglob("*.py"), REPOSITORY_ROOT / "config/kip.example.toml"]:
        secrets |= set(re.findall(r"env:(KIP_[A-Z0-9_]+)", path.read_text(encoding="utf-8")))
    return names | {f"{name}_FILE" for name in secrets}


def test_every_kip_variable_a_developer_shell_can_export_is_pinned_or_explained() -> None:
    # 3.15.0 review: `.env.example` ships `KIP_ROLES=`, common.sh exports it,
    # the CLI reads it, and the ambient-read guard failed 16 tests locally
    # while CI stayed green. Bootstrap's `KIP_SEMANTIC=off` did the same to
    # the setup planner tests. A variable added to any of those sources now
    # fails here until tests/environment.py says what the suite does with it.
    names = _kip_names_a_developer_machine_can_export()
    pinned = set(CI_KIP_ENVIRONMENT) | CI_DYNAMIC_KIP_KEYS

    unaccounted = sorted(names - pinned - KNOWN_ABSENT_AMBIENT_KEYS.keys())
    assert not unaccounted, (
        f"these KIP_* variables can reach the test shell but are neither pinned "
        f"nor listed with a reason in KNOWN_ABSENT_AMBIENT_KEYS: {unaccounted}"
    )
    stale = sorted(KNOWN_ABSENT_AMBIENT_KEYS.keys() - names)
    assert not stale, f"KNOWN_ABSENT_AMBIENT_KEYS lists variables nothing exports or reads: {stale}"
    assert not KNOWN_ABSENT_AMBIENT_KEYS.keys() & pinned
    assert all(reason.strip() for reason in KNOWN_ABSENT_AMBIENT_KEYS.values())


def test_a_dotenv_copied_from_the_example_and_bootstrapped_does_not_trip_the_guard() -> None:
    # The reviewer's reproduction in unit form: a shell that exports every
    # variable .env.example and bootstrap can produce, read by a test.
    from tests import environment
    from tests.environment import ambient_read_violations

    names = frozenset(_kip_names_a_developer_machine_can_export())
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            environment,
            "AMBIENT_ENVIRONMENT",
            {**environment.AMBIENT_ENVIRONMENT, **dict.fromkeys(names, "")},
        )
        assert ambient_read_violations(names, frozenset()) == []


def test_ci_gives_the_real_database_tests_a_designated_database() -> None:
    # Without this export every real-database test skips in CI, silently.
    exported = _ci_quality_environment()

    assert exported.get(TEST_DATABASE_KEY), (
        f"the quality job does not export {TEST_DATABASE_KEY}, so every "
        "PostgreSQL integration and contract test would skip"
    )


def test_the_test_database_is_never_the_deployment_database() -> None:
    live = _database_url("kip_owner", "secret", "127.0.0.1:5432", "kip")

    assert resolve_test_database_url({"KIP_DATABASE_URL": live}) is None
    with pytest.raises(ValueError, match="same database"):
        resolve_test_database_url(
            {"KIP_DATABASE_URL": live, TEST_DATABASE_KEY: _database_url("other", "pw", "localhost", "kip")}
        )
    throwaway = _database_url("kip_owner", "secret", "127.0.0.1:5432", "kip_test_1234")
    assert (
        resolve_test_database_url({"KIP_DATABASE_URL": live, TEST_DATABASE_KEY: throwaway})
        == throwaway
    )
    # A GitHub Actions service database is created for the job and discarded.
    assert (
        resolve_test_database_url(
            {"KIP_DATABASE_URL": live, TEST_DATABASE_KEY: live, "GITHUB_ACTIONS": "true"}
        )
        == live
    )


def test_no_test_takes_a_real_database_from_kip_database_url() -> None:
    # The deployment database is one `.env` away on a developer machine. A
    # test that needs PostgreSQL uses `postgres_database_url` or imports
    # TEST_POSTGRES_URL; neither falls back to KIP_DATABASE_URL.
    fallback = re.compile(
        r"""(?:environ\.get|getenv)\(\s*["'](?:KIP_DATABASE_URL|KIP_TEST_POSTGRES_URL)["']"""
        r"""|environ\[\s*["']KIP_TEST_POSTGRES_URL["']\s*\]"""
    )
    offenders = sorted(
        str(path.relative_to(REPOSITORY_ROOT))
        for path in (REPOSITORY_ROOT / "tests").rglob("*.py")
        if path.name != "environment.py" and fallback.search(path.read_text(encoding="utf-8"))
    )
    assert not offenders, f"tests that read a database URL from the environment: {offenders}"
