from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from kip.adapters.diagnostics import HttpModelRuntimeProbe, KordocOcrRuntimeProbe
from kip.adapters.repository.memory import MemoryRepository
from kip.application import diagnostics as diagnostics_module
from kip.application.diagnostics import (
    _mcp_registration_doctor_check,
    _skill_installs_doctor_check,
)
from kip.cli import app
from kip.container import build_container
from kip.domain.models import Capabilities
from kip.settings import Settings
from kip.setup.planner import build_setup_plan
from kip.setup.writer import apply_setup_plan
from kip.skill_installs import (
    RECORD_FILENAME,
    REGISTRY_PATH,
    REGISTRY_SCHEMA,
    SKILL_NAMES,
    InstallRecord,
    format_install_record,
)
from tests.setup_support import complete_setup_answers


def _semantic_doctor_check(
    settings: Settings, capabilities: Capabilities, verification: dict | None = None
) -> dict:
    """The check as the container wires it: with the real model runtime probe."""
    return diagnostics_module._semantic_doctor_check(
        HttpModelRuntimeProbe(), settings, capabilities, verification
    )


def _kordoc_ocr_doctor_check(settings: Settings) -> dict:
    """The check as the container wires it: with the real Kordoc probe."""
    return diagnostics_module._kordoc_ocr_doctor_check(KordocOcrRuntimeProbe(), settings)


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
    command.write_text("print('4.13.1')", encoding="utf-8")
    settings = _settings(
        tmp_path,
        kordoc={
            "enabled": True,
            "argv": [sys.executable, str(command)],
            "version_argv": [sys.executable, str(command)],
            "expected_version": "4.13.1",
        },
    )

    # When the doctor check runs.
    check = _kordoc_ocr_doctor_check(settings)

    # Then it reports ok and surfaces the detected version.
    assert check["ok"] is True
    assert check["details"] == {"enabled": True, "version": "4.13.1", "reason": None}


def test_kordoc_doctor_check_warns_with_actionable_reason_when_not_resolvable(
    tmp_path: Path,
) -> None:
    # Given Kordoc enabled but not resolvable on PATH (e.g. only installed under
    # a project-local runtime directory, never linked onto PATH).
    settings = _settings(
        tmp_path,
        kordoc={
            "enabled": True,
            "argv": ["kordoc-not-on-path", "--format", "json", "--ocr"],
            "version_argv": ["kordoc-not-on-path", "--version"],
            "expected_version": "4.13.1",
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
            "expected_version": "4.13.1",
        },
    )

    check = _kordoc_ocr_doctor_check(settings)

    assert check["ok"] is False
    assert "expected 4.13.1" in check["details"]["reason"]


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
                        "expected_version": "4.13.1",
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
    assert payload["data"]["summary_en"].startswith("OK:")


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


def test_kordoc_doctor_check_accepts_a_superseded_pin_left_in_a_preserved_config(tmp_path: Path) -> None:
    # Given a deployment config written by an earlier release (expected 4.8.0)
    # after `kip update` installed the currently pinned runtime.
    command = tmp_path / "kordoc_current.py"
    command.write_text("print('4.13.1')", encoding="utf-8")
    settings = _settings(
        tmp_path,
        kordoc={
            "enabled": True,
            "argv": [sys.executable, str(command)],
            "version_argv": [sys.executable, str(command)],
            "expected_version": "4.8.0",
        },
    )

    # When doctor probes the runtime.
    check = _kordoc_ocr_doctor_check(settings)

    # Then the superseded pin means "the pinned runtime" and no config edit is needed.
    assert check["ok"] is True
    assert check["details"]["version"] == "4.13.1"


def _capabilities(*, configured: bool, ready: bool, status: str) -> Capabilities:
    return Capabilities(
        repository="memory",
        lexical_search=True,
        semantic_search=ready,
        semantic_search_configured=configured,
        semantic_projection_status=status,
        graph_backend="memory",
        api=True,
        mcp=True,
        parsers={},
        connectors={},
        warnings=[],
    )


def test_semantic_doctor_check_explains_each_missing_piece(tmp_path: Path) -> None:
    lexical_only = _semantic_doctor_check(_settings(tmp_path, None), _capabilities(configured=False, ready=False, status="disabled"))
    assert lexical_only["ok"] is True and lexical_only["details"]["enabled"] is False

    no_embedding = _semantic_doctor_check(_settings(tmp_path, None), _capabilities(configured=True, ready=False, status="disabled"))
    assert no_embedding["ok"] is False and "models.embedding is disabled" in no_embedding["details"]["reason"]

    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={"models": {"embedding": {"enabled": True, "base_url": "http://127.0.0.1:9"}}},
        environment="test",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )
    unreachable = _semantic_doctor_check(settings, _capabilities(configured=True, ready=False, status="missing"))
    assert unreachable["ok"] is False and unreachable["required"] is False
    assert unreachable["details"]["model_runtime"] is False
    assert "semantic-server.sh start" in unreachable["details"]["reason"]


def _reachable_runtime(monkeypatch, tmp_path: Path) -> tuple[Settings, list[str]]:
    """Embedding enabled, with the model runtime answering its `/models` probe."""
    probed: list[str] = []
    real_client = httpx.Client

    def answer(request: httpx.Request) -> httpx.Response:
        probed.append(str(request.url))
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(answer), **kwargs),
    )
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={"models": {"embedding": {"enabled": True, "base_url": "http://127.0.0.1:9"}}},
        environment="test",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )
    return settings, probed


def test_semantic_doctor_check_reports_a_stale_active_projection(tmp_path: Path, monkeypatch) -> None:
    settings, probed = _reachable_runtime(monkeypatch, tmp_path)
    ready = _capabilities(configured=True, ready=True, status="active")

    check = _semantic_doctor_check(settings, ready, {"ok": False, "indexed_units": 5, "content_units": 7})
    current = _semantic_doctor_check(settings, ready, {"ok": True, "indexed_units": 7, "content_units": 7})

    assert probed == ["http://127.0.0.1:9/models", "http://127.0.0.1:9/models"]
    assert check["ok"] is False and check["required"] is False
    assert check["details"]["model_runtime"] is True
    assert check["details"]["projection"] == "stale"
    assert "holds 5 of 7 visible units" in check["details"]["reason"]
    assert current["ok"] is True and current["details"]["projection"] == "active"


def test_semantic_doctor_check_never_promises_auto_activation_over_an_explicit_space(
    tmp_path: Path, monkeypatch
) -> None:
    settings, _probed = _reachable_runtime(monkeypatch, tmp_path)

    check = _semantic_doctor_check(settings, _capabilities(configured=True, ready=False, status="incompatible"))

    reason = check["details"]["reason"]
    assert check["ok"] is False and check["details"]["projection"] == "incompatible"
    assert "outside the release-reviewed identities is never replaced automatically" in reason
    assert "projection activate --report REPORT --candidate VARIANT" in reason
    assert "to embed and activate it" not in reason


def _runtime_serving(
    monkeypatch, tmp_path: Path, served: list[str], raw_models: dict, raw: dict | None = None
) -> Settings:
    """A reachable runtime whose `/models` answer lists `served`."""
    real_client = httpx.Client

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": model_id} for model_id in served]})

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(answer), **kwargs),
    )
    return Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={"models": raw_models, **(raw or {})},
        environment="test",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )


def test_semantic_doctor_check_separates_a_wrong_model_from_an_unreachable_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    # Given a runtime started with another model (the documented
    # KIP_EMBEDDING_MODEL override, or a stale unit file).
    settings = _runtime_serving(
        monkeypatch,
        tmp_path,
        ["kip-arctic-embed-l-v2-ko"],
        {
            "embedding": {
                "enabled": True,
                "base_url": "http://127.0.0.1:9",
                "model": "kip-qwen3-embedding-0.6b",
            }
        },
    )

    check = _semantic_doctor_check(settings, _capabilities(configured=True, ready=True, status="active"))

    # Then the runtime counts as reachable, and the reason names both models
    # instead of reading like an outage.
    assert check["ok"] is False and check["required"] is False
    assert check["details"]["model_runtime"] is True
    reason = check["details"]["reason"]
    assert "serves ['kip-arctic-embed-l-v2-ko']" in reason
    assert "not the configured models.embedding.model 'kip-qwen3-embedding-0.6b'" in reason
    assert "KIP_EMBEDDING_SERVED_MODEL" in reason and "falls back to lexical" in reason
    assert "no revision or weight hash" in reason
    assert "models.embedding.revision stays unverified at runtime" in reason
    assert "semantic-server.sh start" not in reason


def test_semantic_doctor_check_reports_a_wrong_cross_encoder_model(tmp_path: Path, monkeypatch) -> None:
    settings = _runtime_serving(
        monkeypatch,
        tmp_path,
        ["kip-qwen3-embedding-0.6b"],
        {
            "embedding": {
                "enabled": True,
                "base_url": "http://127.0.0.1:9",
                "model": "kip-qwen3-embedding-0.6b",
            },
            "reranker": {
                "enabled": True,
                "backend": "http",
                "base_url": "http://127.0.0.1:9",
                "model": "kip-bge-reranker-v2-m3",
            },
        },
    )

    check = _semantic_doctor_check(settings, _capabilities(configured=True, ready=True, status="active"))

    assert check["ok"] is False
    reason = check["details"]["reason"]
    assert "not the configured models.reranker.model 'kip-bge-reranker-v2-m3'" in reason
    assert "Reranked mode fails until then" in reason


def test_semantic_doctor_check_passes_when_the_runtime_serves_both_configured_models(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _runtime_serving(
        monkeypatch,
        tmp_path,
        ["kip-qwen3-embedding-0.6b", "kip-bge-reranker-v2-m3"],
        {
            "embedding": {
                "enabled": True,
                "base_url": "http://127.0.0.1:9",
                "model": "kip-qwen3-embedding-0.6b",
            },
            "reranker": {
                "enabled": True,
                "backend": "http",
                "model": "kip-bge-reranker-v2-m3",
            },
        },
    )

    check = _semantic_doctor_check(settings, _capabilities(configured=True, ready=True, status="active"))

    assert check["ok"] is True and check["details"]["reason"] is None


def test_semantic_doctor_check_reports_a_runtime_that_serves_nothing(tmp_path: Path, monkeypatch) -> None:
    settings = _runtime_serving(
        monkeypatch,
        tmp_path,
        [],
        {"embedding": {"enabled": True, "base_url": "http://127.0.0.1:9", "model": "kip-qwen3-embedding-0.6b"}},
    )

    check = _semantic_doctor_check(settings, _capabilities(configured=True, ready=True, status="active"))

    assert check["ok"] is False and check["details"]["model_runtime"] is True
    reason = check["details"]["reason"]
    assert "answers but serves no model" in reason and "semantic-server.sh wait" in reason


def test_semantic_doctor_check_never_probes_a_base_url_egress_forbids(tmp_path: Path, monkeypatch) -> None:
    # The adapters refuse a remote model URL while egress is closed; doctor
    # must report that instead of connecting to it.
    connected: list[str] = []
    real_client = httpx.Client

    def answer(request: httpx.Request) -> httpx.Response:
        connected.append(str(request.url))
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(answer), **kwargs),
    )
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "models": {
                "embedding": {
                    "enabled": True,
                    "base_url": "https://models.example.com",
                    "model": "kip-qwen3-embedding-0.6b",
                }
            }
        },
        environment="test",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )

    check = _semantic_doctor_check(settings, _capabilities(configured=True, ready=True, status="active"))

    assert check["ok"] is False and connected == []
    reason = check["details"]["reason"]
    assert "models.embedding.base_url is not usable" in reason and "loopback" in reason


@pytest.mark.parametrize(
    "reranker",
    [
        {"enabled": False, "backend": "http", "model": "kip-bge-reranker-v2-m3"},
        {"enabled": True, "backend": "bm25"},
        {"enabled": True, "backend": "rapidfuzz", "model": "kip-bge-reranker-v2-m3"},
    ],
)
def test_semantic_doctor_check_only_verifies_a_cross_encoder_taken_from_a_runtime(
    tmp_path: Path, monkeypatch, reranker: dict
) -> None:
    settings = _runtime_serving(
        monkeypatch,
        tmp_path,
        ["kip-qwen3-embedding-0.6b"],
        {
            "embedding": {
                "enabled": True,
                "base_url": "http://127.0.0.1:9",
                "model": "kip-qwen3-embedding-0.6b",
            },
            "reranker": reranker,
        },
    )

    check = _semantic_doctor_check(settings, _capabilities(configured=True, ready=True, status="active"))

    assert check["ok"] is True and check["details"]["reason"] is None


def test_semantic_doctor_check_reports_a_cross_encoder_runtime_of_its_own(tmp_path: Path, monkeypatch) -> None:
    # A reranker on another loopback port: the embedding probe says nothing
    # about it, so it is probed separately and reported as unreachable.
    real_client = httpx.Client

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.port == 9:
            return httpx.Response(200, json={"data": [{"id": "kip-qwen3-embedding-0.6b"}]})
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(answer), **kwargs),
    )
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "models": {
                "embedding": {
                    "enabled": True,
                    "base_url": "http://127.0.0.1:9",
                    "model": "kip-qwen3-embedding-0.6b",
                },
                "reranker": {
                    "enabled": True,
                    "backend": "http",
                    "base_url": "http://127.0.0.1:10",
                    "model": "kip-bge-reranker-v2-m3",
                },
            }
        },
        environment="test",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )

    check = _semantic_doctor_check(settings, _capabilities(configured=True, ready=True, status="active"))

    assert check["ok"] is False and check["details"]["model_runtime"] is True
    assert "reranker model runtime not reachable at http://127.0.0.1:10" in check["details"]["reason"]


_LEGACY_MCP_JSON = {
    "mcpServers": {
        "kip": {
            "command": "bash",
            "args": ["scripts/mcp.sh"],
            "env": {"KIP_CONFIG": "config/kip.host.generated.toml", "KIP_WORKSPACE": "acme-rnd"},
        }
    }
}


def test_mcp_registration_check_flags_the_relative_entry_older_setup_wrote(tmp_path: Path) -> None:
    # Given the relative .mcp.json setup used to write, which upgrades preserve.
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(json.dumps(_LEGACY_MCP_JSON), encoding="utf-8")
    before = mcp_json.read_bytes()

    # When the doctor check runs.
    check = _mcp_registration_doctor_check(_settings(tmp_path, kordoc=None))

    # Then it warns, names each relative path and gives the absolute entry to use.
    assert check["name"] == "mcp_registration"
    assert check["ok"] is False
    assert check["required"] is False
    details = check["details"]
    assert details["relative_paths"] == ["scripts/mcp.sh", "config/kip.host.generated.toml"]
    assert "No such file or directory" in details["reason"]
    assert "never rewrites" in details["fix"]
    assert details["replacement"] == {
        "command": "bash",
        "args": [str(tmp_path / "scripts/mcp.sh")],
        "env": {
            "KIP_CONFIG": str(tmp_path / "config/kip.host.generated.toml"),
            "KIP_WORKSPACE": "acme-rnd",
        },
    }
    # And the operator's file is left exactly as it was.
    assert mcp_json.read_bytes() == before


def test_doctor_command_warns_about_a_relative_mcp_registration_without_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps(_LEGACY_MCP_JSON), encoding="utf-8")
    settings = _settings(tmp_path, kordoc=None)
    # Every other check passes, so the summary names this warning.
    settings.config_path.write_text("", encoding="utf-8")
    settings.cas_path.mkdir(parents=True, exist_ok=True)
    container = build_container(settings, repository=MemoryRepository())
    monkeypatch.setattr("kip.cli.build_container", lambda settings, load_models=True: container)

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    check = next(item for item in payload["data"]["checks"] if item["name"] == "mcp_registration")
    assert check["ok"] is False
    assert "mcp_registration" not in payload["data"]["required_failures"]
    assert "mcp_registration" in payload["data"]["summary"]


@pytest.mark.parametrize(
    "registration",
    [
        None,
        {"mcpServers": {"kip": {"command": "kip", "args": ["mcp"]}}},
        # common.sh exports KIP_PROJECT_ROOT, so a relative config beside an
        # absolute script resolves from any directory.
        {"mcpServers": {"kip": {"command": "bash", "args": ["/srv/kip/scripts/mcp.sh"],
                                "env": {"KIP_CONFIG": "config/kip.toml"}}}},
        # Client-expanded values are not rewritten against the root.
        {"mcpServers": {"kip": {"command": "bash", "args": ["${KIP_HOME}/scripts/mcp.sh"]}}},
    ],
    ids=["no-mcp-json", "launcher", "absolute-script-relative-config", "client-expanded"],
)
def test_mcp_registration_check_passes_entries_that_work_from_any_directory(
    tmp_path: Path, registration: dict[str, object] | None
) -> None:
    if registration is not None:
        (tmp_path / ".mcp.json").write_text(json.dumps(registration), encoding="utf-8")

    check = _mcp_registration_doctor_check(_settings(tmp_path, kordoc=None))

    assert check["ok"] is True
    assert check["details"]["relative_paths"] == []


def test_mcp_registration_check_passes_the_entry_setup_writes(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project_root)
    apply_setup_plan(plan, project_root=project_root)

    check = _mcp_registration_doctor_check(_settings(project_root, kordoc=None))

    assert check["ok"] is True, check
    assert check["details"]["relative_paths"] == []


def _record_skill_install(deployment: Path, destination: Path, version: str) -> None:
    for skill in SKILL_NAMES:
        (destination / skill).mkdir(parents=True)
        record = InstallRecord(skill, deployment, version, "claude", "project", "2026-09-13T00:00:00Z")
        (destination / skill / RECORD_FILENAME).write_text(format_install_record(record), encoding="utf-8")
    registry = deployment / REGISTRY_PATH
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps({
            "schema": REGISTRY_SCHEMA,
            "installs": [{"destination": str(destination), "client": "claude", "scope": "project"}],
        }),
        encoding="utf-8",
    )


@pytest.mark.parametrize(("installed", "expected_ok"), [("3.0.0", True), ("2.9.0", False)], ids=["current", "stale"])
def test_skill_installs_check_warns_only_about_stale_copies(
    tmp_path: Path, installed: str, expected_ok: bool
) -> None:
    # Given a deployment at 3.0.0 that installed its skills into a project.
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    (deployment / "VERSION").write_text("3.0.0\n", encoding="utf-8")
    _record_skill_install(deployment, tmp_path / "project/.claude/skills", installed)

    # When the doctor check runs.
    check = _skill_installs_doctor_check(_settings(deployment, kordoc=None))

    # Then only a copy from another version fails the non-required check, with the fix.
    assert check["name"] == "skill_installs" and check["required"] is False
    assert check["ok"] is expected_ok
    assert {item["state"] for item in check["details"]["installs"]} == {"current" if expected_ok else "stale"}
    assert ("fix" in check["details"]) is not expected_ok


def test_skill_installs_check_does_not_warn_about_a_removed_location(tmp_path: Path) -> None:
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    (deployment / "VERSION").write_text("3.0.0\n", encoding="utf-8")
    destination = tmp_path / "project/.claude/skills"
    _record_skill_install(deployment, destination, "2.9.0")
    shutil.rmtree(tmp_path / "project")

    check = _skill_installs_doctor_check(_settings(deployment, kordoc=None))

    assert check["ok"] is True
    assert {item["state"] for item in check["details"]["installs"]} == {"missing"}


def test_semantic_doctor_check_calls_configured_off_the_intended_lexical_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kip.application.diagnostics import _doctor_summary

    # The environment never decides it: only the configuration disables semantic search.
    monkeypatch.setenv("KIP_SEMANTIC", "on")
    explicit = Settings(
        project_root=tmp_path, config_path=tmp_path / "kip.toml", raw={"search": {"semantic_enabled": False}},
        environment="test", database_url="memory://", cas_path=tmp_path / "cas",
    )
    check = _semantic_doctor_check(explicit, _capabilities(configured=False, ready=False, status="disabled"))

    # Backward-compatible keys stay; the state and message are added.
    assert check["ok"] is True
    assert {key: check["details"][key] for key in ("enabled", "model_runtime", "projection", "reason")} == {
        "enabled": False, "model_runtime": None, "projection": "disabled", "reason": None,
    }
    assert check["details"]["state"] == "disabled_by_configuration"
    disabled_by = "search.semantic_enabled = false (set by configuration; KIP_SEMANTIC=off at install writes this)"
    assert check["details"]["disabled_by"] == disabled_by
    assert check["details"]["message"] == (
        f"semantic search is disabled by configuration: {disabled_by}; lexical search is the intended mode"
    )
    required = {"name": "configuration", "ok": True, "required": True, "details": {}}
    assert _doctor_summary([required, check], []) == (
        f"정상: 필수 점검 1/1 통과. 시맨틱 검색은 설정으로 꺼져 있습니다: {disabled_by}. "
        "lexical 검색이 의도된 모드입니다."
    )

    unset = _semantic_doctor_check(_settings(tmp_path, None), _capabilities(configured=False, ready=False, status="disabled"))
    assert unset["details"]["disabled_by"] == "search.semantic_enabled is not set (defaults to false)"


class _FakeOperations:
    def __init__(self, result: tuple[str | None, str | None] | None = None, error: Exception | None = None) -> None:
        self.result, self.error, self.asked = result, error, []

    def extension_versions(self, name: str) -> tuple[str | None, str | None] | None:
        self.asked.append(name)
        if self.error is not None:
            raise self.error
        return self.result


def test_postgres_extensions_check_is_not_applicable_on_the_memory_repository(tmp_path: Path) -> None:
    from kip.application.diagnostics import _postgres_extensions_doctor_check

    container = build_container(_settings(tmp_path, None), repository=MemoryRepository())

    check = _postgres_extensions_doctor_check(container.application.operations)

    assert check == {
        "name": "postgres_extensions", "ok": True, "required": False,
        "details": {"extension": "vector", "installed_version": None, "server_version": None,
                    "state": "not_applicable", "reason": None},
    }


def test_postgres_extensions_check_fails_an_outdated_vector_catalog_without_requiring_it() -> None:
    from kip.application.diagnostics import _postgres_extensions_doctor_check

    operations = _FakeOperations(("0.8.2", "0.8.6"))
    check = _postgres_extensions_doctor_check(operations)

    assert operations.asked == ["vector"]
    assert check["ok"] is False and check["required"] is False
    assert check["details"] == {
        "extension": "vector", "installed_version": "0.8.2", "server_version": "0.8.6", "state": "outdated",
        "reason": (
            "the vector extension catalog (0.8.2) is older than the server's pgvector (0.8.6), "
            "which happens when migrations ran before the image changed"
        ),
        "fix": "./scripts/kip migrate",
    }
    newer = _postgres_extensions_doctor_check(_FakeOperations(("0.8.10", "0.8.6")))
    assert newer["ok"] is False and newer["details"]["state"] == "newer_than_server"
    assert "fix" in newer["details"] and newer["details"]["fix"] != "./scripts/kip migrate"


def test_postgres_extensions_check_passes_a_current_catalog_and_reports_both_versions() -> None:
    from kip.application.diagnostics import _postgres_extensions_doctor_check

    check = _postgres_extensions_doctor_check(_FakeOperations(("0.8.6", "0.8.6")))
    assert check["ok"] is True
    assert check["details"] == {
        "extension": "vector", "installed_version": "0.8.6", "server_version": "0.8.6",
        "state": "current", "reason": None,
    }
    missing = _postgres_extensions_doctor_check(_FakeOperations((None, "0.8.6")))
    assert missing["ok"] is True and missing["details"]["state"] == "not_installed"


def test_postgres_extensions_check_reports_a_query_failure_as_not_checked() -> None:
    from kip.application.diagnostics import _postgres_extensions_doctor_check
    from kip.errors import DependencyUnavailableError

    check = _postgres_extensions_doctor_check(_FakeOperations(error=DependencyUnavailableError("PostgreSQL is not reachable")))

    assert check["ok"] is True and check["required"] is False
    assert check["details"]["state"] == "not_checked"
    assert "PostgreSQL is not reachable" in check["details"]["error"]


def _port_environment(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name in (
        "KIP_POSTGRES_PORT",
        "KIP_DATABASE_PORT_CHECK",
        "KIP_DATABASE_URL",
        "KIP_BACKUP_DATABASE_URL",
        "KIP_DATABASE_URL_FILE",
        "KIP_BACKUP_DATABASE_URL_FILE",
    ):
        if name in values:
            monkeypatch.setenv(name, values[name])
        else:
            monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("values", "generated", "skipped"),
    [
        ({"KIP_DATABASE_URL": "postgresql://kip:test-password@127.0.0.1:55432/kip"}, False,
         "KIP_POSTGRES_PORT is not set"),
        ({"KIP_POSTGRES_PORT": "5432", "KIP_DATABASE_PORT_CHECK": "off",
          "KIP_DATABASE_URL": "postgresql://kip:test-password@127.0.0.1:55432/kip"}, False,
         "KIP_DATABASE_PORT_CHECK=off"),
        ({"KIP_POSTGRES_PORT": "5432",
          "KIP_DATABASE_URL": "postgresql://kip:test-password@127.0.0.1:55432/kip"}, True,
         "generated deployment (setup verify checks it)"),
    ],
)
def test_database_port_check_is_skipped_where_the_bash_guard_does_not_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str], generated: bool, skipped: str
) -> None:
    from kip.application.diagnostics import _database_port_doctor_check

    _port_environment(monkeypatch, **values)
    if generated:
        (tmp_path / "compose.generated.yaml").write_text("services: {}\n", encoding="utf-8")

    check = _database_port_doctor_check(tmp_path)

    assert check == {
        "name": "database_url_port", "ok": True, "required": False,
        "details": {"reason": None, "skipped": skipped},
    }


@pytest.mark.parametrize(
    ("name", "url", "port"),
    [
        ("KIP_DATABASE_URL", "postgresql://kip:test-password@127.0.0.1:55432/kip", 55432),
        ("KIP_DATABASE_URL", "postgresql://kip:test-password@LOCALHOST/kip", 5432),
        ("KIP_BACKUP_DATABASE_URL", "postgresql://kip_backup:test-password@[::1]:05433/kip", 5433),
    ],
)
def test_database_port_check_fails_a_loopback_url_on_another_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, url: str, port: int
) -> None:
    from kip.application.diagnostics import _database_port_doctor_check

    values = {"KIP_POSTGRES_PORT": "15432", name: url}
    if name != "KIP_DATABASE_URL":
        values["KIP_DATABASE_URL"] = "postgresql://kip:test-password@localhost:15432/kip"
    _port_environment(monkeypatch, **values)

    check = _database_port_doctor_check(tmp_path)

    assert check["ok"] is False and check["required"] is True
    assert check["details"] == {
        "reason": (
            f"{name} uses port {port}, but this deployment publishes PostgreSQL on 15432 "
            f"(KIP_POSTGRES_PORT); another deployment may own port {port}"
        ),
        "fix": (
            "set the same port in KIP_DATABASE_URL and KIP_BACKUP_DATABASE_URL in .env, "
            "or KIP_DATABASE_PORT_CHECK=off for a deliberately separate local PostgreSQL"
        ),
    }


@pytest.mark.parametrize(
    "values",
    [
        {"KIP_POSTGRES_PORT": "05432", "KIP_DATABASE_URL": "postgresql://kip:test-password@127.0.0.1:5432/kip",
         "KIP_BACKUP_DATABASE_URL": "postgresql://kip_backup:test-password@localhost/kip"},
        {"KIP_POSTGRES_PORT": "5432", "KIP_DATABASE_URL": "postgresql://kip:test-password@db.example.test:6543/kip",
         "KIP_BACKUP_DATABASE_URL": "postgresql://kip:test-password@10.0.0.5:6543/kip"},
        {"KIP_POSTGRES_PORT": "5432", "KIP_DATABASE_URL": "memory://"},
    ],
    ids=["matching-port", "external-host", "no-network-url"],
)
def test_database_port_check_passes_a_matching_or_non_loopback_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, values: dict[str, str]
) -> None:
    from kip.application.diagnostics import _database_port_doctor_check

    _port_environment(monkeypatch, **values)

    assert _database_port_doctor_check(tmp_path) == {
        "name": "database_url_port", "ok": True, "required": True, "details": {"reason": None},
    }


def test_database_port_check_reads_a_file_when_the_env_url_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kip.application.diagnostics import _database_port_doctor_check

    secret = tmp_path / "database-url"
    secret.write_text("postgresql://kip:test-password@127.0.0.1:55432/kip\n", encoding="utf-8")
    _port_environment(
        monkeypatch,
        KIP_POSTGRES_PORT="15432",
        KIP_DATABASE_URL_FILE=str(secret),
    )

    check = _database_port_doctor_check(tmp_path)

    assert check["ok"] is False and check["required"] is True
    assert "KIP_DATABASE_URL uses port 55432" in check["details"]["reason"]


def test_database_port_check_fails_a_published_port_that_is_not_a_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kip.application.diagnostics import _database_port_doctor_check

    _port_environment(monkeypatch, KIP_POSTGRES_PORT="54a2",
                      KIP_DATABASE_URL="postgresql://kip:test-password@127.0.0.1:5432/kip")

    assert _database_port_doctor_check(tmp_path) == {
        "name": "database_url_port", "ok": False, "required": True,
        "details": {"reason": "KIP_POSTGRES_PORT='54a2' is not a port number"},
    }


def test_postgres_extensions_check_does_not_order_a_non_numeric_version() -> None:
    from kip.application.diagnostics import _postgres_extensions_doctor_check

    check = _postgres_extensions_doctor_check(_FakeOperations(("0.8.7-dev", "0.8.6")))

    # A development build may be newer: report both versions, advise nothing.
    assert check["ok"] is True and check["required"] is False
    assert check["details"] == {
        "extension": "vector", "installed_version": "0.8.7-dev", "server_version": "0.8.6",
        "state": "unknown_version", "reason": None,
    }


def test_postgres_extensions_check_fails_an_installed_extension_the_server_cannot_load() -> None:
    from kip.application.diagnostics import _postgres_extensions_doctor_check

    check = _postgres_extensions_doctor_check(_FakeOperations(("0.8.6", None)))

    assert check["ok"] is False and check["required"] is False
    assert check["details"] == {
        "extension": "vector", "installed_version": "0.8.6", "server_version": None,
        "state": "installed_but_unavailable",
        "reason": "the vector extension is installed (0.8.6) but this server has no pgvector",
        "fix": "run the pgvector PostgreSQL image the database was created with",
    }
    # The older adapter shape cannot tell an unavailable server apart: it stays not_available.
    assert _postgres_extensions_doctor_check(_FakeOperations((None, None)))["details"]["state"] == "not_available"


@pytest.mark.parametrize(
    ("name", "url", "raw"),
    [
        ("KIP_DATABASE_URL", "postgresql://kip:test-password@127.0.0.1:99999/kip", "99999"),
        ("KIP_DATABASE_URL", "postgresql://kip:test-password@db.example.test:54a2/kip", "54a2"),
        ("KIP_BACKUP_DATABASE_URL", "postgresql://kip_backup:test-password@[::1]:abc/kip", "abc"),
    ],
)
def test_database_port_check_fails_a_url_whose_port_is_not_a_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, url: str, raw: str
) -> None:
    from kip.application.diagnostics import _database_port_doctor_check

    values = {"KIP_POSTGRES_PORT": "5432", name: url}
    if name != "KIP_DATABASE_URL":
        values["KIP_DATABASE_URL"] = "postgresql://kip:test-password@localhost:5432/kip"
    _port_environment(monkeypatch, **values)

    check = _database_port_doctor_check(tmp_path)

    assert check["ok"] is False and check["required"] is True
    assert check["details"] == {
        "reason": f"{name} has an invalid port ({raw!r}), so it cannot connect",
        "fix": f"set a port number from 1 to 65535 in {name} in .env",
    }


def test_database_port_check_reports_an_explicit_port_zero_instead_of_defaulting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kip.application.diagnostics import _database_port_doctor_check

    _port_environment(monkeypatch, KIP_POSTGRES_PORT="5432",
                      KIP_DATABASE_URL="postgresql://kip:test-password@127.0.0.1:0/kip")

    check = _database_port_doctor_check(tmp_path)

    assert check["ok"] is False
    assert check["details"]["reason"].startswith("KIP_DATABASE_URL uses port 0, but this deployment publishes")
