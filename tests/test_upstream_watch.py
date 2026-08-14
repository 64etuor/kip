from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WATCH_SCRIPT = ROOT / "scripts/check-upstream-updates.sh"


def test_upstream_watch_reports_new_kordoc_from_ocr_pin(tmp_path: Path) -> None:
    # Given the OCR adapter pin and newer upstream Kordoc metadata.
    config = tmp_path / "kip.toml"
    config.write_text(
        """
[parsers.hwp.kordoc]
argv = ["kordoc", "{input}"]

[parsers.ocr.kordoc]
expected_version = "4.7.3"
""".strip(),
        encoding="utf-8",
    )
    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "KIP_EMBEDDING_REVISION=embedding-current\n"
        "KIP_RERANKER_REVISION=reranker-current\n",
        encoding="utf-8",
    )
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
url="${@: -1}"
case "$url" in
  */kordoc/latest) printf '{"version":"4.8.0"}\\n' ;;
  */Qwen/Qwen3-Embedding-0.6B) printf '{"sha":"embedding-current"}\\n' ;;
  */BAAI/bge-reranker-v2-m3) printf '{"sha":"reranker-current"}\\n' ;;
  *) exit 64 ;;
esac
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    # When the reusable update detector runs.
    result = subprocess.run(
        [str(WATCH_SCRIPT)],
        cwd=ROOT,
        env={
            **os.environ,
            "KIP_UPSTREAM_CONFIG": str(config),
            "KIP_UPSTREAM_ENV_FILE": str(environment_file),
            "KIP_UPSTREAM_CURL": str(fake_curl),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    # Then it reports the reviewed OCR pin, not an unrelated command literal.
    assert result.returncode == 0, result.stderr
    assert result.stdout == "- `kordoc`: `4.7.3` -> `4.8.0`\n"


def test_upstream_watch_is_quiet_when_all_pins_match(tmp_path: Path) -> None:
    # Given local parser and model pins that match their upstream metadata.
    config = tmp_path / "kip.toml"
    config.write_text(
        '[parsers.ocr.kordoc]\nexpected_version = "4.7.3"\n',
        encoding="utf-8",
    )
    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "KIP_EMBEDDING_REVISION=embedding-current\n"
        "KIP_RERANKER_REVISION=reranker-current\n",
        encoding="utf-8",
    )
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
url="${@: -1}"
case "$url" in
  */kordoc/latest) printf '{"version":"4.7.3"}\\n' ;;
  */Qwen/Qwen3-Embedding-0.6B) printf '{"sha":"embedding-current"}\\n' ;;
  */BAAI/bge-reranker-v2-m3) printf '{"sha":"reranker-current"}\\n' ;;
  *) exit 64 ;;
esac
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    # When the reusable update detector runs.
    result = subprocess.run(
        [str(WATCH_SCRIPT)],
        cwd=ROOT,
        env={
            **os.environ,
            "KIP_UPSTREAM_CONFIG": str(config),
            "KIP_UPSTREAM_ENV_FILE": str(environment_file),
            "KIP_UPSTREAM_CURL": str(fake_curl),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    # Then it emits no false update notification.
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_upstream_watch_workflow_runs_detector_daily() -> None:
    # Given the checked-in update notification workflow.
    workflow = yaml.load(
        (ROOT / ".github/workflows/upstream-watch.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )

    # When its schedule and detector step are inspected.
    schedule = workflow["on"]["schedule"]
    steps = workflow["jobs"]["detect"]["steps"]
    detector_run = next(step["run"] for step in steps if "run" in step)

    # Then every daily run uses the behavior-tested detector.
    assert schedule == [{"cron": "0 0 * * *"}]
    assert './scripts/check-upstream-updates.sh > "$report"' in detector_run
