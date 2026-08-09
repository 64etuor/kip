from __future__ import annotations

from pathlib import Path

import pytest

from kip.errors import ConfigurationError
from kip.settings import Settings


def _base_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KIP_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("KIP_ENV", "test")
    monkeypatch.delenv("KIP_DATABASE_URL", raising=False)
    monkeypatch.delenv("KIP_DATABASE_URL_FILE", raising=False)


def test_settings_loads_database_url_from_secret_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base_environment(monkeypatch, tmp_path)
    secret = tmp_path / "database-url"
    secret.write_text("postgresql://kip_api:redacted@postgres/kip\n", encoding="utf-8")
    monkeypatch.setenv("KIP_DATABASE_URL_FILE", str(secret))

    settings = Settings.load()

    assert settings.database_url == "postgresql://kip_api:redacted@postgres/kip"


def test_settings_rejects_ambiguous_or_unsafe_secret_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _base_environment(monkeypatch, tmp_path)
    secret = tmp_path / "database-url"
    secret.write_text("memory://\n", encoding="utf-8")
    monkeypatch.setenv("KIP_DATABASE_URL", "memory://")
    monkeypatch.setenv("KIP_DATABASE_URL_FILE", str(secret))

    with pytest.raises(ConfigurationError, match="both"):
        Settings.load()

    monkeypatch.delenv("KIP_DATABASE_URL")
    linked = tmp_path / "linked-secret"
    linked.symlink_to(secret)
    monkeypatch.setenv("KIP_DATABASE_URL_FILE", str(linked))
    with pytest.raises(ConfigurationError, match="symlink"):
        Settings.load()


def test_file_secret_reference_uses_the_same_bounded_reader(tmp_path: Path) -> None:
    secret = tmp_path / "provider-key"
    secret.write_text("provider-key-value\n", encoding="utf-8")
    settings = Settings.for_test()

    assert settings.resolve_secret_reference(f"file:{secret}") == "provider-key-value"

    multiline = tmp_path / "multiline"
    multiline.write_text("first\nsecond\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="single line"):
        settings.resolve_secret_reference(f"file:{multiline}")
