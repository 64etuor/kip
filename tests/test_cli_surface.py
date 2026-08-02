from pathlib import Path

from typer.testing import CliRunner

from kip.cli import app

ROOT = Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    return {
        "KIP_CONFIG": str(ROOT / "config/kip.example.toml"),
        "KIP_DATABASE_URL": "memory://",
        "KIP_PROJECT_ROOT": str(ROOT),
        "KIP_ENV": "test",
    }


def test_cli_exposes_agent_and_application_compatibility_commands():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"], env=_env())
    assert result.exit_code == 0
    for command in ["doctor", "sync", "xlsx-read", "projection", "export", "explain", "evaluate"]:
        assert command in result.stdout


def test_source_neutral_sync_run_supports_dry_run():
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["sync", "run", "--source", "sample", "--dry-run"],
        env=_env(),
    )
    assert result.exit_code == 0, result.stdout
    assert '"ok": true' in result.stdout
    assert '"source": "sample"' in result.stdout


def test_projection_and_export_command_groups_are_stable(tmp_path: Path):
    runner = CliRunner()
    projection_help = runner.invoke(app, ["projection", "--help"], env=_env())
    assert projection_help.exit_code == 0, projection_help.stdout
    assert "activate" in projection_help.stdout

    projection = runner.invoke(app, ["projection", "status"], env=_env())
    assert projection.exit_code == 0, projection.stdout
    assert '"lexical"' in projection.stdout

    output = tmp_path / "canonical.jsonl"
    export = runner.invoke(
        app,
        [
            "--config",
            str(ROOT / "config/kip.example.toml"),
            "export",
            "canonical",
            "--output",
            str(output),
        ],
        env={**_env(), "KIP_PROJECT_ROOT": str(ROOT)},
    )
    assert export.exit_code == 0, export.stdout
    assert output.is_file()


def test_evaluate_validate_and_run_preserve_json_envelope(tmp_path: Path):
    dataset = tmp_path / "golden.yaml"
    dataset.write_text(
        """
schema_version: kip.golden-dataset.v1
name: cli-fixture
corpus_fingerprint: sha256:fixture
cases:
  - id: GQ-001
    question: 존재하지 않는 문서
    category: access_denied
    principal: principal_public
    acl_scopes: [workspace:default]
    expected_documents: []
    forbidden_documents: [doc_secret]
    recall_at: 10
""".strip()
        + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()

    validated = runner.invoke(
        app,
        ["evaluate", "validate", "--dataset", str(dataset)],
        env=_env(),
    )
    assert validated.exit_code == 0, validated.stdout
    assert '"ok": true' in validated.stdout
    assert '"case_count": 1' in validated.stdout

    output = tmp_path / "reports"
    executed = runner.invoke(
        app,
        [
            "evaluate",
            "run",
            "--dataset",
            str(dataset),
            "--variants",
            "lexical",
            "--output-dir",
            str(output),
        ],
        env=_env(),
    )
    assert executed.exit_code == 0, executed.stdout
    assert '"ok": true' in executed.stdout
    assert (output / "latest.json").is_file()
