from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kip.adapters.repository.memory import MemoryRepository
from kip.cli import _kordoc_ocr_doctor_check, app
from kip.container import build_container
from kip.settings import Settings


def _settings(tmp_path: Path, kordoc: dict[str, object] | None) -> Settings:
    raw: dict[str, object] = {}
    if kordoc is not None:
        raw = {"parsers": {"ocr": {"kordoc": kordoc}}}
    return Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw=raw,
        environment="test",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )


def test_kordoc_doctor_check_is_inert_when_disabled(tmp_path: Path) -> None:
    # Given Kordoc OCR left disabled (or unconfigured).
    settings = _settings(tmp_path, kordoc=None)

    # When the doctor check runs.
    check = _kordoc_ocr_doctor_check(settings)

    # Then it reports ok without probing any subprocess.
    assert check["name"] == "kordoc_ocr_resolvable"
    assert check["ok"] is True
    assert check["required"] is False
    assert check["details"] == {"enabled": False, "version": None, "reason": None}


def test_kordoc_doctor_check_is_inert_when_explicitly_disabled(tmp_path: Path) -> None:
    settings = _settings(tmp_path, kordoc={"enabled": False})

    check = _kordoc_ocr_doctor_check(settings)

    assert check["ok"] is True
    assert check["details"]["enabled"] is False


def test_kordoc_doctor_check_reports_ok_with_detected_version_when_resolvable(
    tmp_path: Path,
) -> None:
    # Given an enabled Kordoc runtime whose version probe resolves and matches.
    command = tmp_path / "kordoc_ok.py"
    command.write_text("print('4.7.3')", encoding="utf-8")
    settings = _settings(
        tmp_path,
        kordoc={
            "enabled": True,
            "argv": [sys.executable, str(command)],
            "version_argv": [sys.executable, str(command)],
            "expected_version": "4.7.3",
        },
    )

    # When the doctor check runs.
    check = _kordoc_ocr_doctor_check(settings)

    # Then it reports ok and surfaces the detected version.
    assert check["ok"] is True
    assert check["details"] == {"enabled": True, "version": "4.7.3", "reason": None}


def test_kordoc_doctor_check_warns_with_actionable_reason_when_not_resolvable(
    tmp_path: Path,
) -> None:
    # Given Kordoc enabled but not resolvable on PATH (e.g. only installed under
    # var/kordoc/node_modules/.bin/kordoc, never linked onto PATH).
    settings = _settings(
        tmp_path,
        kordoc={
            "enabled": True,
            "argv": ["kordoc-not-on-path", "--format", "json", "--ocr"],
            "version_argv": ["kordoc-not-on-path", "--version"],
            "expected_version": "4.7.3",
        },
    )

    # When the doctor check runs.
    check = _kordoc_ocr_doctor_check(settings)

    # Then it warns (not required) with an actionable reason instead of
    # letting image-bearing PDF/PPTX silently degrade to partial later.
    assert check["ok"] is False
    assert check["required"] is False
    assert check["details"]["version"] is None
    reason = check["details"]["reason"]
    assert reason is not None
    assert "not resolvable on PATH" in reason
    assert "scripts/install-kordoc.sh" in reason
    assert "disable parsers.ocr.kordoc" in reason


def test_kordoc_doctor_check_warns_on_version_mismatch(tmp_path: Path) -> None:
    command = tmp_path / "kordoc_wrong_version.py"
    command.write_text("print('4.7.2')", encoding="utf-8")
    settings = _settings(
        tmp_path,
        kordoc={
            "enabled": True,
            "argv": [sys.executable, str(command)],
            "version_argv": [sys.executable, str(command)],
            "expected_version": "4.7.3",
        },
    )

    check = _kordoc_ocr_doctor_check(settings)

    assert check["ok"] is False
    assert "expected 4.7.3" in check["details"]["reason"]


def test_doctor_command_surfaces_kordoc_resolvability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given a running container whose configuration enables Kordoc OCR with a
    # runtime that is not resolvable.
    source_root = tmp_path / "source"
    source_root.mkdir()
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "sources": {"filesystem": []},
            "parsers": {
                "hwp": {"order": ["paired_pdf"]},
                "ocr": {
                    "kordoc": {
                        "enabled": True,
                        "argv": ["kordoc-not-on-path", "--format", "json", "--ocr"],
                        "version_argv": ["kordoc-not-on-path", "--version"],
                        "expected_version": "4.7.3",
                    }
                },
            },
        },
        environment="test",
        database_url="memory://",
        cas_path=tmp_path / "cas",
        api_key="test-key",
        admin_key="test-admin",
    )
    container = build_container(settings, repository=MemoryRepository())
    monkeypatch.setattr(
        "kip.cli.build_container",
        lambda settings, load_models=True: container,
    )

    # When `kip doctor` runs.
    result = CliRunner().invoke(app, ["doctor"])

    # Then the OCR resolvability warning surfaces without failing the run
    # (it is not a required check).
    assert result.exit_code == 0, result.stdout
    assert '"kordoc_ocr_resolvable"' in result.stdout
    assert "not resolvable on PATH" in result.stdout

    # And the payload carries a plain-language Korean verdict a non-expert
    # operator can act on without decoding `content_units`/`checks`.
    payload = json.loads(result.stdout)
    summary = payload["data"]["summary"]
    assert summary.startswith("정상:")
    assert "경고" in summary


def test_doctor_summary_is_clean_when_every_check_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Every required check ok, and the one informational check (a config
    # file on disk) present too, so `doctor` has zero optional warnings.
    config_path = tmp_path / "kip.toml"
    config_path.write_text("", encoding="utf-8")
    settings = Settings(
        project_root=tmp_path,
        config_path=config_path,
        raw={"sources": {"filesystem": []}},
        environment="test",
        database_url="memory://",
        cas_path=tmp_path / "cas",
        api_key="test-key",
        admin_key="test-admin",
    )
    settings.cas_path.mkdir(parents=True, exist_ok=True)
    container = build_container(settings, repository=MemoryRepository())
    monkeypatch.setattr(
        "kip.cli.build_container",
        lambda settings, load_models=True: container,
    )

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    summary = payload["data"]["summary"]
    assert summary.startswith("정상:")
    assert "통과" in summary
    assert "경고" not in summary
    assert "문제" not in summary
