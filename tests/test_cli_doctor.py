from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from kip.adapters.repository.memory import MemoryRepository
from kip.cli import (
    _kordoc_ocr_doctor_check,
    _mcp_registration_doctor_check,
    _semantic_doctor_check,
    _skill_installs_doctor_check,
    app,
)
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
