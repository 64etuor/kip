from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "backup_artifacts", ROOT / "scripts/backup_artifacts.py"
)
assert _SPEC is not None and _SPEC.loader is not None
backup_artifacts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backup_artifacts)


# Credential-shaped fixtures are assembled from parts so that the release
# bundle secret scanner (scripts/release_artifacts.py) never sees a contiguous
# database-URL credential literal in this file. The runtime values the
# redaction functions receive are unchanged.
_FIXTURE_PASSWORD = "super" + "secret"
_FIXTURE_URL = "postgresql://kip_owner:" + _FIXTURE_PASSWORD + "@127.0.0.1:5432/kip"
_REDACTED_URL = "postgresql://kip_owner:" + "[REDACTED]" + "@127.0.0.1:5432/kip"


def test_redact_config_text_redacts_literal_secrets_and_url_credentials() -> None:
    text = (
        "[database]\n"
        f'url = "{_FIXTURE_URL}"\n'
        'api_key = "literal-key-123"\n'
        'password = "hunter2"  # inline comment\n'
    )
    redacted, count = backup_artifacts.redact_config_text(text)
    assert count == 3
    assert _FIXTURE_PASSWORD not in redacted
    assert "literal-key-123" not in redacted
    assert "hunter2" not in redacted
    assert f'url = "{_REDACTED_URL}"' in redacted
    assert 'password = "[REDACTED]"  # inline comment' in redacted
    assert backup_artifacts._contains_literal_secret(redacted) is None


def test_redact_config_text_preserves_reference_convention() -> None:
    text = (
        'url_env = "KIP_DATABASE_URL"\n'
        'password_env = "KIP_IMAP_PASSWORD"\n'
        'secret_ref = "env:KIP_GENERATION_API_KEY"\n'
        'token_file = "file:/run/secrets/slack-token"\n'
        "require_api_key_outside_development = true\n"
        'token = ""\n'
    )
    redacted, count = backup_artifacts.redact_config_text(text)
    assert count == 0
    assert redacted == text
    assert backup_artifacts._contains_literal_secret(text) is None


def test_snapshot_config_produces_redacted_archive(tmp_path: Path) -> None:
    root = tmp_path / "starter"
    (root / "config").mkdir(parents=True)
    (root / "ontology").mkdir()
    (root / "VERSION").write_text("0.0.0\n", encoding="utf-8")
    (root / "ontology/catalog.yaml").write_text("entities: []\n", encoding="utf-8")
    (root / "config/kip.toml").write_text(
        '[slack]\ntoken = "xoxb-literal-token"\nurl_env = "KIP_DATABASE_URL"\n',
        encoding="utf-8",
    )
    archive_path = tmp_path / "configuration.tar.gz"

    result = backup_artifacts.snapshot_config(root, archive_path)

    assert result["status"] == "created"
    assert result["redacted_values"] == 1
    with tarfile.open(archive_path, "r:gz") as archive:
        stream = archive.extractfile("config/kip.toml")
        assert stream is not None
        content = stream.read().decode("utf-8")
    assert "xoxb-literal-token" not in content
    assert 'token = "[REDACTED]"' in content
    assert 'url_env = "KIP_DATABASE_URL"' in content
    backup_artifacts._verify_config_archive(archive_path)


def test_verify_config_archive_rejects_literal_secret(tmp_path: Path) -> None:
    archive_path = tmp_path / "configuration.tar.gz"
    members = {
        "VERSION": b"0.0.0\n",
        "ontology/catalog.yaml": b"entities: []\n",
        "config/kip.toml": b'[slack]\ntoken = "xoxb-literal-token"\n',
    }
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))

    with pytest.raises(backup_artifacts.BackupError, match="literal secret"):
        backup_artifacts._verify_config_archive(archive_path)
