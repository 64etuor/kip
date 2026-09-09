from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("explicit_url", [False, True])
@pytest.mark.parametrize("curl_fails", [False, True])
def test_smoke_uses_configured_endpoint_identity_and_project_python(
    tmp_path: Path, explicit_url: bool, curl_fails: bool,
) -> None:
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for name in ("curl-smoke.sh", "common.sh", "load_dotenv.py"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    (project / ".venv/bin").mkdir(parents=True)
    project_python = project / ".venv/bin/python"
    project_python.write_text('#!/bin/bash\nexec "$SMOKE_REAL_PYTHON" "$@"\n')
    project_python.chmod(0o755)
    (project / "config").mkdir()
    (project / "config/kip.toml").write_text(
        '[identity.api_key]\napi_key_env="SMOKE_SELECTED_KEY"\n'
    )
    key = secrets.token_urlsafe(24)
    (project / ".env").write_text(
        f"KIP_API_PORT=57735\nKIP_API_KEY={secrets.token_urlsafe(24)}\n"
        f"SMOKE_SELECTED_KEY={key}\nKIP_DATABASE_URL=memory://\n"
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    calls_path = tmp_path / "calls.jsonl"
    curl = binary / "curl"
    curl.write_text(
        f"#!{sys.executable}\nimport json,os,sys\n"
        "with open(os.environ['SMOKE_CALLS'],'a') as log: log.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "if os.environ['SMOKE_FAIL']=='1': raise SystemExit(22)\n"
        "print(json.dumps({'ok':True}))\n"
    )
    curl.chmod(0o755)
    # A system-Python fallback would fail instead of silently passing this test.
    python3 = binary / "python3"
    python3.write_text("#!/bin/bash\nexit 99\n")
    python3.chmod(0o755)
    environment = {
        **{key: value for key, value in os.environ.items() if not key.startswith("KIP_")},
        "PATH": f"{binary}:/usr/bin:/bin", "PYTHONPATH": str(ROOT / "src"),
        "SMOKE_CALLS": str(calls_path), "SMOKE_FAIL": "1" if curl_fails else "0",
        "SMOKE_REAL_PYTHON": sys.executable,
    }
    if explicit_url:
        environment["KIP_API_URL"] = "http://127.0.0.1:59999/custom/"
    result = subprocess.run(
        ["bash", str(scripts / "curl-smoke.sh")], cwd=project,
        env=environment, capture_output=True, text=True,
    )
    assert bool(result.returncode) is curl_fails
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    base = "http://127.0.0.1:59999/custom" if explicit_url else "http://127.0.0.1:57735"
    assert [arguments[-1] for arguments in calls] == (
        [f"{base}/readyz"] if curl_fails else [f"{base}/readyz", f"{base}/v1/capabilities"]
    )
    for arguments in calls:
        assert f"X-KIP-API-Key: {key}" in arguments
        assert not any("X-KIP-Workspace" in value or "X-KIP-Principal" in value for value in arguments)
    assert key not in result.stdout + result.stderr
