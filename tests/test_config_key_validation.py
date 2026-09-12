"""A config key this build does not read must be visible, not ignored.

Nothing validated configuration keys, so a misspelling or a key left behind
by an older release changed nothing and said nothing. Startup still never
fails on one — an operator locked out by a stale key would be worse — but the
keys are reported once through the same capability warnings that carry the
non-durable repository warning.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from kip.settings import Settings, unknown_config_keys
from kip.setup.config_payload import build_config_payload
from kip.setup.planner import build_setup_plan
from tests.setup_support import complete_setup_answers

ROOT = Path(__file__).resolve().parents[1]

_VALID_CONFIG = """
[app]
environment = "test"
workspace = "default"

[search]
semantic_enabled = false
default_mode = "hybrid"

[[sources.filesystem]]
name = "fixture"
root = "./sample-data"
enabled = true
acl_scope = "workspace:default"
"""


def _load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> Settings:
    monkeypatch.setenv("KIP_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("KIP_ENV", "test")
    monkeypatch.delenv("KIP_DATABASE_URL", raising=False)
    monkeypatch.delenv("KIP_DATABASE_URL_FILE", raising=False)
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    path = config / "kip.toml"
    path.write_text(body, encoding="utf-8")
    # CI exports KIP_CONFIG for the whole job, so the file under test has to be
    # named explicitly or Settings.load reads the repository's own config.
    monkeypatch.setenv("KIP_CONFIG", str(path))
    return Settings.load()


def test_a_misspelled_key_is_collected_without_failing_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _load(
        monkeypatch,
        tmp_path,
        _VALID_CONFIG.replace(
            'default_mode = "hybrid"',
            'default_mode = "hybrid"\nlexical_limit_default = 10',
        )
        + '\n[serach]\ndefault_mode = "hybrid"\n',
    )

    assert settings.workspace == "default"
    assert settings.unknown_config_keys == (
        "search.lexical_limit_default",
        "serach.default_mode",
    )


def test_a_valid_config_reports_no_unknown_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _load(monkeypatch, tmp_path, _VALID_CONFIG)

    assert settings.unknown_config_keys == ()


def test_unknown_keys_reach_the_operator_through_capability_warnings(
    test_container,
) -> None:
    test_container.settings.unknown_config_keys = ("serach.default_mode",)

    warnings = test_container.application.operations.capabilities().warnings

    assert any("serach.default_mode" in warning for warning in warnings)
    assert sum("not recognised" in warning for warning in warnings) == 1


def test_the_shipped_configs_and_the_setup_writer_stay_recognised(
    tmp_path: Path,
) -> None:
    """The list of recognised keys cannot rot silently.

    Every key this project ships — in the example config, in the container
    config, and in the payload `kip setup` writes — must be one the code
    claims to read; otherwise a stock deployment would warn about its own
    configuration.
    """
    plan = build_setup_plan(
        complete_setup_answers(tmp_path),
        project_root=tmp_path / "project",
    )

    for name in ("kip.example.toml", "kip.container.toml"):
        raw = tomllib.loads((ROOT / "config" / name).read_text(encoding="utf-8"))
        assert unknown_config_keys(raw) == (), name
    for container in (False, True):
        payload = build_config_payload(plan, container=container)
        assert unknown_config_keys(payload) == (), container


def test_an_empty_source_array_is_not_reported_as_unrecognised() -> None:
    from kip.settings import unknown_config_keys

    assert unknown_config_keys({"sources": {"filesystem": []}}) == ()
    assert unknown_config_keys({"sources": {"filesystem": [{"name": "a"}]}}) == ()
    assert "sources.filesystem[].nonsense" in unknown_config_keys(
        {"sources": {"filesystem": [{"nonsense": 1}]}}
    )
