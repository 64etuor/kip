from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

URL = os.environ.get("KIP_TEST_POSTGRES_URL") or os.environ.get("KIP_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not URL, reason="PostgreSQL integration URL not configured")


def test_private_gate_skips_or_fails_closed_over_an_unrelated_indexed_corpus() -> None:
    psycopg = pytest.importorskip("psycopg")
    if not (ROOT / "evaluation/golden/private-onedrive-nl.floor.json").exists():
        pytest.skip("reviewed private floor is not part of this checkout")
    workspace = "golden_" + uuid.uuid4().hex[:12]
    environment = {**os.environ, "KIP_WORKSPACE": workspace, "KIP_DATABASE_URL": str(URL)}
    try:
        synced = subprocess.run(
            [sys.executable, "-m", "kip.cli", "sync", "run", "--source", "sample"],
            cwd=ROOT, env=environment, capture_output=True, text=True, check=False,
        )
        assert synced.returncode == 0, synced.stderr
        assert '"inserted": 3' in synced.stdout

        # A durable, non-empty corpus that is not the reviewed one is a skip,
        # not a zero-recall regression.
        skipped = subprocess.run(
            [sys.executable, "scripts/golden_gate.py"],
            cwd=ROOT, env=environment, capture_output=True, text=True, check=False,
        )
        assert skipped.returncode == 0, skipped.stdout + skipped.stderr
        assert "reviewed corpus is not indexed in this workspace; skipping" in skipped.stdout
        assert "recall@k" not in skipped.stdout

        required = subprocess.run(
            [sys.executable, "scripts/golden_gate.py"],
            cwd=ROOT, env={**environment, "KIP_REQUIRE_PRIVATE_GOLDEN": "1"},
            capture_output=True, text=True, check=False,
        )
        assert required.returncode == 1
        assert "reviewed corpus is not indexed in this workspace; FAILED" in required.stdout
    finally:
        with psycopg.connect(str(URL), autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM kip.workspaces WHERE slug=%s", (workspace,))
