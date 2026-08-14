from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from kip.adapters.parsers.pdf import PdfParser
from kip.adapters.parsers.pptx import PptxParser
from kip.adapters.parsers.registry import ParserRegistry
from kip.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "config_path",
    [ROOT / "config/kip.example.toml", ROOT / "config/kip.container.toml"],
)
def test_reference_configs_enable_pinned_kordoc_ocr_by_default(
    config_path: Path,
) -> None:
    # Given a reference configuration delivered to a new KIP installation.
    settings = Settings.load(config_path)

    # When the parser registry is composed from that configuration.
    registry = ParserRegistry.from_settings(settings)
    pdf = next(parser for parser in registry.parsers if isinstance(parser, PdfParser))
    pptx = next(parser for parser in registry.parsers if isinstance(parser, PptxParser))
    kordoc = settings.get("parsers.ocr.kordoc", {}) or {}

    # Then Korean OCR is active and pinned for both image-bearing formats.
    assert kordoc["enabled"] is True
    assert kordoc["expected_version"] == "4.7.3"
    assert pdf._ocr is not None
    assert pptx._ocr is not None


def test_kordoc_launcher_uses_preinstalled_package_and_offline_model_cache(
    tmp_path: Path,
) -> None:
    # Given a project-local Kordoc package that records its runtime environment.
    package = tmp_path / "kordoc"
    cli = package / "dist/cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text(
        "console.log(JSON.stringify({args: process.argv.slice(2), "
        "cache: process.env.KORDOC_MODEL_CACHE, "
        "offline: process.env.KORDOC_OFFLINE}))\n",
        encoding="utf-8",
    )

    # When the checked-in launcher invokes the preinstalled package.
    result = subprocess.run(
        [str(ROOT / "scripts/kordoc"), "--version"],
        cwd=tmp_path,
        env={
            **os.environ,
            "KIP_KORDOC_PACKAGE_DIR": str(package),
            "KORDOC_MODEL_CACHE": str(tmp_path / "models"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    # Then parsing cannot trigger a package download and runs offline by default.
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "args": ["--version"],
        "cache": str(tmp_path / "models"),
        "offline": "1",
    }


def test_common_runtime_resolves_checked_in_kordoc_launcher() -> None:
    # Given the shell environment used by KIP CLI, API, and worker launchers.
    command = 'source scripts/common.sh; command -v kordoc'

    # When the runtime resolves the configured OCR command.
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then it selects the checked-in offline launcher from any working directory.
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == ROOT / "scripts/kordoc"


def test_kordoc_installer_verifies_version_and_prewarms_models(tmp_path: Path) -> None:
    # Given an exact local package whose model check records the configured cache.
    install_root = tmp_path / "install"
    package = install_root / "node_modules/kordoc"
    cli = package / "dist/cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text(
        """
const fs = require("node:fs")
if (process.argv.includes("--version")) {
  console.log("4.7.3")
} else if (process.argv.includes("check-ocr-models")) {
  fs.mkdirSync(process.env.KORDOC_MODEL_CACHE, {recursive: true})
  fs.writeFileSync(process.env.KORDOC_MODEL_CACHE + "/ready", "true")
}
""".strip(),
        encoding="utf-8",
    )
    model_cache = tmp_path / "models"

    # When the default installer validates and prepares Kordoc.
    result = subprocess.run(
        [str(ROOT / "scripts/install-kordoc.sh")],
        cwd=ROOT,
        env={
            **os.environ,
            "KIP_KORDOC_INSTALL_ROOT": str(install_root),
            "KORDOC_MODEL_CACHE": str(model_cache),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    # Then the verified package prewarms the selected model cache.
    assert result.returncode == 0, result.stderr
    assert (model_cache / "ready").read_text(encoding="utf-8") == "true"


def test_bootstrap_and_container_bake_the_same_pinned_kordoc_runtime() -> None:
    # Given the local bootstrap and production image definitions.
    bootstrap = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    # When their Kordoc installation commands are inspected.
    bootstrap_installer = '"$SCRIPT_DIR/install-kordoc.sh"'
    pinned_package = '"kordoc@${KORDOC_VERSION}"'

    # Then both default runtimes install 4.7.3 and prewarm Korean OCR models.
    assert bootstrap_installer in bootstrap
    assert "ARG KORDOC_VERSION=4.7.3" in dockerfile
    assert pinned_package in dockerfile
    assert "kordoc check-ocr-models" in dockerfile
    assert "KORDOC_OFFLINE=1" in dockerfile


def test_doctor_requires_the_default_kordoc_runtime_and_korean_models() -> None:
    doctor = (ROOT / "scripts/doctor.sh").read_text(encoding="utf-8")
    required_checks = (
        'required "Node 18+ for Kordoc OCR"',
        'required "Kordoc 4.7.3"',
        'required "Kordoc PP-OCRv5 Korean models"',
    )

    assert all(check in doctor for check in required_checks)
