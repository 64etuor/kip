from __future__ import annotations

import subprocess
from pathlib import Path

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


def test_semantic_bootstrap_is_isolated_and_version_pinned() -> None:
    script = (ROOT / "scripts" / "bootstrap-semantic.sh").read_text(encoding="utf-8")

    assert "var/semantic-venv" in script
    assert "infinity-emb[server,torch]==0.0.77" in script
    assert "click==8.1.8" in script
    assert ".venv" not in script


def test_example_configuration_keeps_semantic_search_disabled() -> None:
    config = (ROOT / "config" / "kip.example.toml").read_text(encoding="utf-8")

    assert "semantic_enabled = false" in config
    assert "[models.embedding]" in config
    assert "[models.reranker]" in config
    assert 'base_url = "http://127.0.0.1:7997"' in config
    assert "allow_remote_model_egress = false" in config


def test_doctor_checks_optional_semantic_runtime() -> None:
    doctor = (ROOT / "scripts" / "doctor.sh").read_text(encoding="utf-8")

    assert "semantic model environment" in doctor
    assert "semantic model server" in doctor
    assert "pgvector extension" in doctor
