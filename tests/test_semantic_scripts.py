from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_semantic_scripts_are_shell_valid_and_loopback_only() -> None:
    scripts = [
        ROOT / "scripts" / "bootstrap-semantic.sh",
        ROOT / "scripts" / "semantic-server.sh",
        ROOT / "scripts" / "semantic-smoke.sh",
    ]
    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], check=True)

    server = scripts[1].read_text(encoding="utf-8")
    assert "127.0.0.1" in server
    assert "0.0.0.0" not in server
    assert "Qwen/Qwen3-Embedding-0.6B" in server
    assert "BAAI/bge-reranker-v2-m3" in server
    assert "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3" in server
    assert "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e" in server
    assert "--no-bettertransformer" in server
    assert "KIP_EMBEDDING_SERVER_BATCH_SIZE" in server
    assert "KIP_RERANKER_SERVER_BATCH_SIZE" in server


def test_semantic_bootstrap_is_isolated_and_hash_locked() -> None:
    script = (ROOT / "scripts" / "bootstrap-semantic.sh").read_text(encoding="utf-8")
    lock = (ROOT / "requirements" / "semantic.txt").read_text(encoding="utf-8")
    source = (ROOT / "requirements" / "semantic.in").read_text(encoding="utf-8")

    assert "var/semantic-venv" in script
    assert "--require-hashes" in script and "requirements/semantic.txt" in script
    assert ".venv\"" not in script
    assert "infinity-emb[server,torch]==0.0.77" in source and "click==8.1.8" in source
    assert "infinity-emb==0.0.77 \\" in lock and "--hash=sha256:" in lock
    assert "uv pip compile requirements/semantic.in" in lock


def test_semantic_runtime_audit_lists_every_reviewed_advisory_with_a_reason() -> None:
    audit = (ROOT / "scripts" / "audit-semantic.sh").read_text(encoding="utf-8")
    verify = (ROOT / "scripts" / "verify.sh").read_text(encoding="utf-8")

    assert "requirements/semantic.txt" in audit and "--ignore-vuln" in audit
    for advisory in ("PYSEC-2026-2132", "PYSEC-2025-217", "PYSEC-2026-3929"):
        assert advisory in audit
    assert "audit-semantic.sh" in verify


def test_shipped_configurations_enable_the_reviewed_semantic_default() -> None:
    from kip.application.semantic import (
        EMBEDDING_DEFAULTS,
        RELEASE_REVIEWED_EMBEDDING_IDENTITIES,
        SEMANTIC_DEFAULT_MODE,
    )

    reviewed = RELEASE_REVIEWED_EMBEDDING_IDENTITIES[0]
    for name, host in (("kip.example.toml", "127.0.0.1"), ("kip.container.toml", "models")):
        with (ROOT / "config" / name).open("rb") as handle:
            config = tomllib.load(handle)
        search = config["search"]
        embedding = config["models"]["embedding"]
        assert search["semantic_enabled"] is True, name
        assert search["default_mode"] == SEMANTIC_DEFAULT_MODE, name
        assert embedding["enabled"] is True, name
        assert embedding["base_url"] == f"http://{host}:7997", name
        for key, value in EMBEDDING_DEFAULTS.items():
            assert embedding[key] == value, (name, key)
        assert (embedding["model"], embedding["revision"], embedding["dimensions"]) == (
            reviewed.model,
            reviewed.revision,
            reviewed.dimensions,
        )
        assert embedding["max_document_chars"] == reviewed.max_document_chars
        assert embedding["query_instruction"] == reviewed.query_instruction
        assert config["security"]["allow_remote_model_egress"] is False, name
    with (ROOT / "config" / "kip.container.toml").open("rb") as handle:
        assert tomllib.load(handle)["security"]["model_service_hosts"] == ["models"]


def test_compose_models_service_is_digest_pinned_and_isolated() -> None:
    development = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    production = yaml.safe_load((ROOT / "compose.production.yaml").read_text(encoding="utf-8"))
    for payload in (development, production):
        models = payload["services"]["models"]
        assert "@sha256:" in models["image"]
        assert "0.0.0.0" in models["command"] and "--revision" in models["command"]
        assert "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3" in models["command"]
    assert production["services"]["models"]["networks"] == ["models"]
    assert production["networks"]["models"]["internal"] is True
    assert production["services"]["models"]["environment"]["HF_HUB_OFFLINE"] == "1"
    assert production["services"]["models-fetch"]["profiles"] == ["models-fetch"]
    assert "models" in production["services"]["api"]["networks"]
    assert "models" in production["services"]["worker"]["networks"]


def test_doctor_checks_optional_semantic_runtime() -> None:
    doctor = (ROOT / "scripts" / "doctor.sh").read_text(encoding="utf-8")

    assert "semantic model environment" in doctor
    assert "semantic model server" in doctor
    assert "pgvector extension" in doctor


def test_uninstall_launchd_removes_every_label_install_can_write() -> None:
    install = (ROOT / "scripts" / "install-launchd.sh").read_text(encoding="utf-8")
    uninstall = (ROOT / "scripts" / "uninstall-launchd.sh").read_text(encoding="utf-8")

    written = set(re.findall(r'"Label": "([^"]+)"', install))
    for array in re.findall(r"labels\+?=\(([^)]*)\)", install):
        written.update(array.split())
    loop = re.search(r"for label in ([^;]+); do", uninstall)
    assert loop is not None
    assert "com.kip.semantic" in written
    assert written <= set(loop.group(1).split())


_FAKE_CURL = r"""#!/bin/bash
count=$(( $(cat "$FAKE_STATE/curl" 2>/dev/null || echo 0) + 1 ))
echo "$count" > "$FAKE_STATE/curl"
plan="${FAKE_CURL_PLAN:-0}"
index=$count
(( index <= ${#plan} )) || index=${#plan}
[[ "${plan:index-1:1}" == 1 ]] || exit 7
printf '%s' "${FAKE_CURL_BODY:-{\"data\":[{\"id\":\"kip-qwen3-embedding-0.6b\"}]}}"
"""
_FAKE_PGREP = r"""#!/bin/bash
count=$(( $(cat "$FAKE_STATE/pgrep" 2>/dev/null || echo 0) + 1 ))
echo "$count" > "$FAKE_STATE/pgrep"
plan="${FAKE_PGREP_PLAN:-1}"
index=$count
(( index <= ${#plan} )) || index=${#plan}
[[ -n "${FAKE_PGREP_PID:-}" && "${plan:index-1:1}" == 1 ]] || exit 1
echo "$FAKE_PGREP_PID"
"""
_FAKE_DOCKER = r"""#!/bin/bash
printf 'docker %s\n' "$*" >> "$FAKE_TRACE"
case " $* " in *" ps "*) printf '%s' "${FAKE_MODELS_CONTAINER:-}" ;; esac
"""
_FAKE_UNAME = r"""#!/bin/bash
if [[ "${1:-}" == -m && -n "${FAKE_ARCH:-}" ]]; then echo "$FAKE_ARCH"; exit 0; fi
exec env PATH=/usr/bin:/bin uname "$@"
"""


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _semantic_project(tmp_path: Path, **fake: str) -> tuple[Path, dict[str, str], Path]:
    """A checkout copy whose runtime, curl, pgrep, docker and uname are fakes."""
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    for name in ("semantic-server.sh", "app-up.sh", "common.sh"):
        shutil.copy2(ROOT / "scripts" / name, project / "scripts" / name)
    venv = project / "var/semantic-venv/bin"
    venv.mkdir(parents=True)
    fakes = tmp_path / "bin"
    fakes.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    trace = tmp_path / "trace"
    executables = {
        venv / "infinity_emb": '#!/bin/bash\nprintf "infinity %s\\n" "$*" >> "$FAKE_TRACE"\n',
        venv / "python": '#!/bin/bash\nprintf "python\\n" >> "$FAKE_TRACE"\nexit 1\n',
        fakes / "curl": _FAKE_CURL,
        fakes / "pgrep": _FAKE_PGREP,
        fakes / "docker": _FAKE_DOCKER,
        fakes / "uname": _FAKE_UNAME,
    }
    for path, body in executables.items():
        path.write_text(body)
        path.chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("KIP_", "FAKE_"))}
    environment.update({
        "PATH": f"{fakes}:{os.environ['PATH']}", "KIP_SKIP_DOTENV": "1",
        # A closed port, so a real runtime on this machine is never probed.
        "KIP_SEMANTIC_PORT": str(_closed_port()),
        "FAKE_STATE": str(state), "FAKE_TRACE": str(trace), **fake,
    })
    return project, environment, trace


def _script(project: Path, environment: dict[str, str], name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(project / "scripts" / name), *args],
        cwd=project, env=environment, capture_output=True, text=True, timeout=60,
    )


def _trace(trace: Path) -> list[str]:
    return trace.read_text().splitlines() if trace.exists() else []


@pytest.mark.parametrize("busy", [
    {"FAKE_CURL_PLAN": "110"},
    {"FAKE_PGREP_PID": "4242", "FAKE_PGREP_PLAN": "10"},
])
def test_run_waits_while_another_runtime_serves_then_takes_over(tmp_path: Path, busy: dict[str, str]) -> None:
    project, environment, trace = _semantic_project(tmp_path, KIP_SEMANTIC_RECHECK_SECONDS="0", **busy)
    result = _script(project, environment, "semantic-server.sh", "run")
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("not loading a second copy") == 1
    if "FAKE_PGREP_PID" in busy:
        assert "(PID 4242)" in result.stderr
    assert "is free; starting the model runtime" in result.stderr
    calls = _trace(trace)
    assert len(calls) == 2 and calls[0] == "python" and calls[1].startswith("infinity v2 ")


def test_run_starts_at_once_when_the_port_is_free(tmp_path: Path) -> None:
    project, environment, trace = _semantic_project(tmp_path)
    result = _script(project, environment, "semantic-server.sh", "run")
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert _trace(trace)[-1].startswith("infinity v2 ")


@pytest.mark.parametrize("served_reranker", [False, True])
def test_start_with_reranker_checks_what_the_running_instance_serves(tmp_path: Path, served_reranker: bool) -> None:
    body = '{"data":[{"id":"kip-qwen3-embedding-0.6b"}' + (',{"id":"kip-bge-reranker-v2-m3"}' if served_reranker else "") + "]}"
    project, environment, trace = _semantic_project(
        tmp_path, KIP_SEMANTIC_RERANKER="on", FAKE_PGREP_PID="4242", FAKE_CURL_PLAN="1", FAKE_CURL_BODY=body,
    )
    result = _script(project, environment, "semantic-server.sh", "start")
    if served_reranker:
        assert result.returncode == 0, result.stderr
        assert "already running with PID 4242" in result.stdout
    else:
        assert result.returncode == 1
        assert "does not serve the reranker" in result.stderr and "stop" in result.stderr
    assert not _trace(trace)


def test_status_and_stop_report_a_supervised_instance_without_python(tmp_path: Path) -> None:
    project, environment, trace = _semantic_project(tmp_path, FAKE_PGREP_PID="4242", FAKE_CURL_PLAN="0")
    finished = subprocess.Popen(["true"])
    finished.wait()
    (project / "var/run").mkdir(parents=True)
    (project / "var/run/semantic-server.pid").write_text(f"{finished.pid}\n")

    status = _script(project, environment, "semantic-server.sh", "status")
    assert status.returncode == 0, status.stderr
    assert "running with PID 4242" in status.stdout and str(finished.pid) not in status.stdout
    assert "not answering yet" in status.stdout
    stop = _script(project, environment, "semantic-server.sh", "stop")
    assert stop.returncode == 0, stop.stderr
    assert "launchctl bootout" in stop.stdout and "systemctl --user stop kip-semantic" in stop.stdout
    assert not _trace(trace)


def test_wait_follows_a_supervised_instance_that_is_still_loading(tmp_path: Path) -> None:
    project, environment, _ = _semantic_project(tmp_path, FAKE_PGREP_PID="4242", FAKE_CURL_PLAN="001")
    result = _script(project, environment, "semantic-server.sh", "wait")
    assert result.returncode == 0, result.stderr
    assert "ready" in result.stdout

    project, environment, _ = _semantic_project(tmp_path / "gone")
    result = _script(project, environment, "semantic-server.sh", "wait")
    assert result.returncode == 1
    assert str(project / "var/log/semantic-server.log") in result.stderr


def _compose_up(trace: Path) -> str:
    return next(call for call in _trace(trace) if call.endswith(" up -d --build"))


def test_app_up_on_amd64_keeps_a_loading_host_runtime_instead_of_compose_models(tmp_path: Path) -> None:
    project, environment, trace = _semantic_project(
        tmp_path, FAKE_ARCH="x86_64", FAKE_PGREP_PID="4242", FAKE_CURL_PLAN="0001",
    )
    result = _script(project, environment, "app-up.sh")
    assert result.returncode == 0, result.stderr
    assert "already running with PID 4242" in result.stdout
    assert "--profile semantic" not in _compose_up(trace)
    assert "host model runtime serves CLI/MCP" in result.stdout


@pytest.mark.parametrize("compose_models", [True, False])
def test_app_up_on_amd64_tells_compose_models_from_another_runtime(tmp_path: Path, compose_models: bool) -> None:
    project, environment, trace = _semantic_project(
        tmp_path, FAKE_ARCH="x86_64", FAKE_CURL_PLAN="1",
        FAKE_MODELS_CONTAINER="0123456789ab" if compose_models else "",
    )
    result = _script(project, environment, "app-up.sh")
    assert result.returncode == 0, result.stderr
    assert any(" ps -q --status running models" in call for call in _trace(trace))
    if compose_models:
        # Keep the profile so an upgrade recreates `models` with the new image.
        assert "--profile semantic" in _compose_up(trace)
        assert "compose models service serves" in result.stdout
        assert "use lexical search" not in result.stdout
    else:
        assert "--profile semantic" not in _compose_up(trace)
        assert "existing model runtime" in result.stdout


def test_start_leaves_a_port_held_by_a_runtime_that_is_still_loading(tmp_path: Path) -> None:
    project, environment, trace = _semantic_project(tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        environment["KIP_SEMANTIC_PORT"] = str(listener.getsockname()[1])
        result = _script(project, environment, "semantic-server.sh", "start")

    assert result.returncode == 0, result.stderr
    assert "already in use" in result.stdout
    assert not [line for line in _trace(trace) if line.startswith("infinity")]


def test_wait_rechecks_the_reranker_once_the_instance_answers(tmp_path: Path) -> None:
    project, environment, _ = _semantic_project(
        tmp_path, KIP_SEMANTIC_RERANKER="on", FAKE_PGREP_PID="4242", FAKE_CURL_PLAN="1",
    )
    result = _script(project, environment, "semantic-server.sh", "wait")

    assert result.returncode == 1
    assert "does not serve the reranker" in result.stderr


def test_wait_follows_a_held_port_until_the_runtime_answers(tmp_path: Path) -> None:
    project, environment, _ = _semantic_project(tmp_path, FAKE_CURL_PLAN="001")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        environment["KIP_SEMANTIC_PORT"] = str(listener.getsockname()[1])
        result = _script(project, environment, "semantic-server.sh", "wait")

    assert result.returncode == 0, result.stderr
    assert "ready" in result.stdout


def test_wait_names_the_port_holder_when_it_never_answers(tmp_path: Path) -> None:
    project, environment, _ = _semantic_project(tmp_path, KIP_SEMANTIC_WAIT_SECONDS="2")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        environment["KIP_SEMANTIC_PORT"] = str(listener.getsockname()[1])
        result = _script(project, environment, "semantic-server.sh", "wait")

    assert result.returncode == 1
    assert "did not become ready" in result.stderr and "holding port" in result.stderr


def test_run_waits_for_a_port_holder_and_takes_over_once_it_is_free(tmp_path: Path) -> None:
    project, environment, trace = _semantic_project(tmp_path, KIP_SEMANTIC_RECHECK_SECONDS="0.2")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    environment["KIP_SEMANTIC_PORT"] = str(listener.getsockname()[1])
    process = subprocess.Popen(
        ["/bin/bash", str(project / "scripts/semantic-server.sh"), "run"],
        cwd=project, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (tmp_path / "state" / "curl").exists():
            time.sleep(0.05)
        time.sleep(0.5)
        assert process.poll() is None and not [line for line in _trace(trace) if line.startswith("infinity")]
    finally:
        listener.close()
    _, stderr = process.communicate(timeout=20)

    assert process.returncode == 0, stderr
    assert "not loading a second copy" in stderr
    assert [line for line in _trace(trace) if line.startswith("infinity")]


def test_runtime_reads_models_from_the_same_hub_cache_prefetch_fills(tmp_path: Path) -> None:
    project, environment, trace = _semantic_project(
        tmp_path, SENTENCE_TRANSFORMERS_HOME="/elsewhere", TRANSFORMERS_CACHE="/elsewhere", HF_HUB_CACHE="/elsewhere",
    )
    runtime = project / "var/semantic-venv/bin/infinity_emb"
    runtime.write_text(
        '#!/bin/bash\nprintf "cache hf=%s hub=%s st=%s tf=%s\\n" "$HF_HOME" "$HF_HUB_CACHE" '
        '"${SENTENCE_TRANSFORMERS_HOME-unset}" "${TRANSFORMERS_CACHE-unset}" >> "$FAKE_TRACE"\n'
    )

    result = _script(project, environment, "semantic-server.sh", "run")

    assert result.returncode == 0, result.stderr
    cache = f"{project}/var/model-cache"
    assert f"cache hf={cache} hub={cache}/hub st=unset tf=unset" in _trace(trace)
