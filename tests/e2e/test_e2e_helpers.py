"""The stdlib checkers the end-to-end scripts rely on fail when they should.

`scripts/e2e-install.sh` and `scripts/e2e-upgrade.sh` only prove something if
`mcp_stdio_probe.py`, `skill_installs.py` and `kip_envelope.py doctor` reject a
wrong answer. These cases feed each checker a correct and a broken input; the
real deployment is exercised by the scripts themselves.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILLS = ("knowledge-fabric", "kip-setup")


def _run(script: str, *arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HERE / script), *arguments],
        capture_output=True, text=True, check=False, cwd=cwd,
    )


# ------------------------------------------------------------ mcp_stdio_probe

_FAKE_SERVER = textwrap.dedent(
    """
    import json, sys
    BANNER = {banner!r}
    if BANNER:
        print(BANNER, flush=True)
    envelope = {{"schema_version": "kip.envelope.v1", "ok": True, "data": {{"repository": "postgresql"}}}}
    for line in sys.stdin:
        message = json.loads(line)
        method, ident = message.get("method"), message.get("id")
        if method == "initialize":
            result = {{"protocolVersion": "2025-06-18", "serverInfo": {{"name": "kip", "version": "9.9.9"}}, "capabilities": {{}}}}
        elif method == "tools/list":
            result = {{"tools": [{{"name": n}} for n in ("kip_capabilities", "kip_search", "kip_read", "kip_xlsx_read")]}}
        elif method == "tools/call":
            result = {{"content": [{{"type": "text", "text": json.dumps(envelope)}}]}}
        else:
            continue
        print(json.dumps({{"jsonrpc": "2.0", "id": ident, "result": result}}), flush=True)
    """
)


def _fake_server(tmp_path: Path, banner: str = "") -> Path:
    server = tmp_path / "server.py"
    server.write_text(_FAKE_SERVER.format(banner=banner), encoding="utf-8")
    return server


def test_probe_accepts_a_server_that_writes_only_json_rpc(tmp_path: Path) -> None:
    server = _fake_server(tmp_path)

    result = _run("mcp_stdio_probe.py", "--cwd", str(tmp_path), "--expect-version", "9.9.9",
                  "--expect-repository", "postgresql", "--timeout", "30", "--", sys.executable, str(server))

    assert result.returncode == 0, result.stderr
    assert "all JSON-RPC" in result.stdout


def test_probe_rejects_a_server_that_prints_anything_else_on_stdout(tmp_path: Path) -> None:
    server = _fake_server(tmp_path, banner="KIP MCP server starting")

    result = _run("mcp_stdio_probe.py", "--cwd", str(tmp_path), "--timeout", "30", "--", sys.executable, str(server))

    assert result.returncode == 1
    assert "not JSON-RPC" in result.stderr


def test_probe_rejects_a_server_of_another_version(tmp_path: Path) -> None:
    server = _fake_server(tmp_path)

    result = _run("mcp_stdio_probe.py", "--cwd", str(tmp_path), "--expect-version", "1.0.0",
                  "--timeout", "30", "--", sys.executable, str(server))

    assert result.returncode == 1
    assert "serverInfo.version" in result.stderr


# ------------------------------------------------------------- skill_installs

def _install(location: Path, deployment: Path, version: str, *, record: bool = True) -> None:
    for skill in SKILLS:
        skill_dir = location / skill
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\nname: {skill}\n---\n", encoding="utf-8")
        if record:
            (skill_dir / ".kip-skill-install").write_text(
                "# Written by KIP\nschema=kip.skill-install.v1\n"
                f"skill={skill}\ndeployment={deployment}\nversion={version}\n"
                "client=claude\nscope=personal\ninstalled_at=2026-09-13T00:00:00Z\n",
                encoding="utf-8",
            )


def test_record_accepts_copies_recorded_for_this_deployment_and_version(tmp_path: Path) -> None:
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    _install(tmp_path / "skills", deployment, "3.15.1")

    result = _run("skill_installs.py", "record", str(tmp_path / "skills"),
                  "--deployment", str(deployment), "--version", "3.15.1", "--client", "claude")

    assert result.returncode == 0, result.stderr


def test_record_rejects_a_copy_recorded_for_another_deployment(tmp_path: Path) -> None:
    (tmp_path / "deployment").mkdir()
    (tmp_path / "other").mkdir()
    _install(tmp_path / "skills", tmp_path / "other", "3.15.1")

    result = _run("skill_installs.py", "record", str(tmp_path / "skills"),
                  "--deployment", str(tmp_path / "deployment"), "--version", "3.15.1")

    assert result.returncode == 1
    assert "deployment is" in result.stderr


def test_record_rejects_a_stale_version(tmp_path: Path) -> None:
    _install(tmp_path / "skills", tmp_path, "3.15.0")

    result = _run("skill_installs.py", "record", str(tmp_path / "skills"),
                  "--deployment", str(tmp_path), "--version", "3.15.1")

    assert result.returncode == 1
    assert "version is '3.15.0'" in result.stderr


def test_unrecorded_rejects_a_copy_that_gained_a_record(tmp_path: Path) -> None:
    _install(tmp_path / "skills", tmp_path, "3.15.1")

    result = _run("skill_installs.py", "unrecorded", str(tmp_path / "skills"))

    assert result.returncode == 1


def test_absent_rejects_a_location_that_still_holds_a_skill(tmp_path: Path) -> None:
    _install(tmp_path / "skills", tmp_path, "3.15.1", record=False)

    result = _run("skill_installs.py", "absent", str(tmp_path / "skills"))

    assert result.returncode == 1
    assert "still holds" in result.stderr


def _registry(deployment: Path, *destinations: Path) -> None:
    (deployment / "var").mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "kip.skill-install-registry.v1",
        "installs": [{"destination": str(item), "client": "claude", "scope": "personal"} for item in destinations],
    }
    (deployment / "var/skill-installs.json").write_text(json.dumps(payload), encoding="utf-8")


def test_registry_rejects_an_extra_location(tmp_path: Path) -> None:
    _registry(tmp_path, tmp_path / "personal", tmp_path / "project")

    result = _run("skill_installs.py", "registry", str(tmp_path), "--expect", str(tmp_path / "personal"))

    assert result.returncode == 1
    assert "expected exactly" in result.stderr


def test_registry_accepts_exactly_the_expected_locations(tmp_path: Path) -> None:
    _registry(tmp_path, tmp_path / "personal")

    result = _run("skill_installs.py", "registry", str(tmp_path), "--expect", str(tmp_path / "personal"))

    assert result.returncode == 0, result.stderr


def test_set_version_makes_a_record_stale(tmp_path: Path) -> None:
    _install(tmp_path / "skills", tmp_path, "3.15.1")

    edited = _run("skill_installs.py", "set-version", str(tmp_path / "skills"), "--skill", "kip-setup", "--version", "0.0.1")
    checked = _run("skill_installs.py", "record", str(tmp_path / "skills"), "--deployment", str(tmp_path), "--version", "3.15.1")

    assert edited.returncode == 0, edited.stderr
    assert checked.returncode == 1
    assert "version is '0.0.1'" in checked.stderr


# ------------------------------------------------------- kip_envelope doctor

def _doctor(tmp_path: Path, config: Path, skill_installs_ok: bool, installs: int) -> Path:
    envelope = {
        "schema_version": "kip.envelope.v1",
        "ok": True,
        "error": None,
        "data": {
            "checks": [
                {"name": "configuration", "ok": True, "required": False, "details": {"path": str(config)}},
                {"name": "skill_installs", "ok": skill_installs_ok, "required": False,
                 "details": {"installs": [{"state": "current"}] * installs}},
            ],
        },
        "meta": {"request_id": "req_1", "workspace": "default", "warnings": []},
    }
    path = tmp_path / "doctor.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("skill_installs_ok", "installs", "config_inside", "expected"),
    [
        (True, 6, True, 0),
        (False, 6, True, 1),
        (True, 4, True, 1),
        (True, 6, False, 1),
    ],
    ids=["current", "stale", "wrong-count", "other-deployment"],
)
def test_doctor_requires_the_named_check_count_and_deployment(
    tmp_path: Path, skill_installs_ok: bool, installs: int, config_inside: bool, expected: int
) -> None:
    deployment = tmp_path / "deployment"
    config = (deployment if config_inside else tmp_path / "elsewhere") / "config/kip.toml"
    path = _doctor(tmp_path, config, skill_installs_ok, installs)

    result = _run("kip_envelope.py", "doctor", str(path), "--deployment", str(deployment),
                  "--check-ok", "skill_installs", "--skill-installs", "6")

    assert result.returncode == expected, result.stderr
