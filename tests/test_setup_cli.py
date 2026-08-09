from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from kip.cli import app


def test_setup_cli_runs_before_runtime_configuration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KIP_ENV", "production")
    missing_config = tmp_path / "missing.toml"
    state = tmp_path / "setup.json"

    result = CliRunner().invoke(
        app,
        [
            "--config",
            str(missing_config),
            "setup",
            "inspect",
            "--project-root",
            str(tmp_path),
            "--state",
            str(state),
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "kip.envelope.v1"
    assert payload["data"]["questions"][0]["id"] == "workspace"


def test_setup_cli_answers_previews_plans_applies_and_verifies(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    source = tmp_path / "company-docs"
    source.mkdir()
    (source / "policy.txt").write_text("승인 정책", encoding="utf-8")
    backup = tmp_path / "backup"
    state = tmp_path / "setup-state.json"
    plan = tmp_path / "setup-plan.json"
    runner = CliRunner()

    answers = [
        ("workspace", "acme-rnd"),
        ("identity_mode", "proxy_jwt"),
        ("identity_owner", "platform-security"),
        ("source_ownership", "company"),
        (
            "filesystem_sources",
            json.dumps(
                [
                    {
                        "name": "company-docs",
                        "root": str(source),
                        "classification": "internal",
                        "acl_scope": "workspace:acme-rnd",
                    }
                ]
            ),
        ),
        ("model_provider", "disabled"),
        ("database_secret_ref", "env:KIP_DATABASE_URL"),
        ("cas_path", str(tmp_path / "cas")),
        ("backup_path", str(backup)),
        ("retention_days", "365"),
        ("sync_schedule", "manual"),
        ("evaluation_dataset", "none"),
        ("ontology_reviewers", '["knowledge-owner"]'),
    ]
    for question, value in answers:
        result = runner.invoke(
            app,
            [
                *_setup_args(project_root, state),
                "answer",
                "--question",
                question,
                "--value",
                value,
            ],
        )
        assert result.exit_code == 0, result.stdout

    preview = runner.invoke(
        app,
        [*_setup_args(project_root, state), "preview"],
    )
    planned = runner.invoke(
        app,
        [*_setup_args(project_root, state), "plan", "--output", str(plan)],
    )
    applied = runner.invoke(
        app,
        [*_setup_args(project_root, state), "apply", "--plan", str(plan)],
    )
    verified = runner.invoke(
        app,
        [*_setup_args(project_root, state), "verify", "--plan", str(plan)],
    )

    assert json.loads(preview.stdout)["data"][0]["file_count"] == 1
    assert planned.exit_code == 0, planned.stdout
    assert applied.exit_code == 0, applied.stdout
    receipt = json.loads(verified.stdout)["data"]
    assert receipt["verified"] is True
    assert receipt["source_summaries"][0]["file_count"] == 1
    assert (project_root / "config/kip.generated.toml").is_file()
    assert (project_root / "compose.generated.yaml").is_file()


def test_setup_cli_rejects_stale_plan_before_apply(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = tmp_path / "state.json"
    plan = tmp_path / "plan.json"
    _write_complete_state(tmp_path, project_root, state)
    runner = CliRunner()
    assert runner.invoke(
        app,
        [*_setup_args(project_root, state), "plan", "--output", str(plan)],
    ).exit_code == 0

    changed = runner.invoke(
        app,
        [
            *_setup_args(project_root, state),
            "answer",
            "--question",
            "workspace",
            "--value",
            "acme-new",
        ],
    )
    applied = runner.invoke(
        app,
        [*_setup_args(project_root, state), "apply", "--plan", str(plan)],
    )

    assert changed.exit_code == 0
    assert applied.exit_code == 3
    assert "stale" in applied.stderr
    assert not (project_root / "config/kip.generated.toml").exists()


def _setup_args(project_root: Path, state: Path) -> list[str]:
    return [
        "setup",
        "--project-root",
        str(project_root),
        "--state",
        str(state),
    ]


def _write_complete_state(
    tmp_path: Path,
    project_root: Path,
    state: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()
    payload = {
        "schema_version": "kip.setup-answers.v1",
        "workspace": "acme-rnd",
        "identity_mode": "proxy_jwt",
        "identity_owner": "platform-security",
        "source_ownership": "company",
        "filesystem_sources": [
            {
                "name": "company-docs",
                "root": str(source.resolve()),
                "classification": "internal",
                "acl_scope": "workspace:acme-rnd",
                "include_extensions": [".txt"],
                "exclude_globs": [],
                "read_only": True,
                "follow_symlinks": False,
            }
        ],
        "model_provider": "disabled",
        "model_egress_classifications": None,
        "model_secret_ref": None,
        "database_secret_ref": {"scheme": "env", "name": "KIP_DATABASE_URL"},
        "cas_path": str((tmp_path / "cas").resolve()),
        "backup_path": str(backup.resolve()),
        "retention_days": 365,
        "sync_schedule": "manual",
        "evaluation_dataset": "none",
        "ontology_reviewers": ["knowledge-owner"],
    }
    state.write_text(json.dumps(payload), encoding="utf-8")
