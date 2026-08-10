import json
import shutil
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
    for command in [
        "doctor",
        "sync",
        "xlsx-read",
        "projection",
        "export",
        "explain",
        "evaluate",
        "quality",
        "ontology",
        "parser",
    ]:
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


def test_parser_reextract_defaults_to_non_mutating_shadow_mode() -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["parser", "reextract", "--source", "sample"],
        env=_env(),
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "kip.envelope.v1"
    assert payload["data"]["activate"] is False
    assert payload["data"]["activated"] == 0


def test_answer_command_returns_versioned_evidence_response() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["answer", "참여율 변경"], env=_env())

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "kip.envelope.v1"
    assert payload["data"]["schema_version"] == "kip.answer.v1"
    assert "citations" in payload["data"]


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


def test_evaluate_run_scores_version_bound_answer_and_ontology_reviews(
    tmp_path: Path,
) -> None:
    output = tmp_path / "rag-reports"

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            "run",
            "--dataset",
            str(ROOT / "evaluation/golden/ontology-starter.yaml"),
            "--reviews",
            str(ROOT / "evaluation/reviews/ontology-starter.yaml"),
            "--variants",
            "hybrid",
            "--warmup-passes",
            "0",
            "--output-dir",
            str(output),
        ],
        env=_env(),
    )

    assert result.exit_code == 0, result.stdout
    report = json.loads((output / "latest.json").read_text(encoding="utf-8"))
    quality = report["variants"]["hybrid"]
    assert quality["answer_quality"]["metrics"]["claim_precision"] == 1.0
    assert quality["ontology_quality"]["metrics"]["relation_recall"] == 1.0
    assert report["run"]["dataset_gate_eligible"] is True
    assert "role:evaluation-owner" not in str(report)


def test_quality_commands_validate_and_recommend_without_activation(tmp_path: Path) -> None:
    # Given a pinned experiment and matching evaluation report
    manifest = ROOT / "evaluation/experiments/example.yaml"
    metrics = {
        "case_count": 2,
        "failed_case_count": 0,
        "recall_at_k": 1.0,
        "mrr": 1.0,
        "ndcg_at_k": 1.0,
        "zero_result_rate": 0.0,
        "unauthorized_result_count": 0,
        "locator_accuracy": 1.0,
        "latest_version_accuracy": 1.0,
        "stale_warning_rate": 1.0,
    }
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "schema_version": "kip.evaluation-report.v1",
                "fingerprints": {
                    "corpus": "sha256:replace-with-corpus-fingerprint",
                    "dataset": "sha256:replace-with-dataset-fingerprint",
                    "configuration": "sha256:replace-with-configuration-fingerprint",
                    "code": "sha256:replace-with-code-fingerprint",
                },
                "variants": {
                    name: {
                        "metrics": metrics,
                        "latency_ms": {"p50": 10.0, "p95": 20.0, "max": 30.0},
                        "categories": {"semantic": metrics, "exact": metrics},
                    }
                    for name in ("hybrid", "reranked")
                },
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()

    # When the operator validates and evaluates the experiment
    validated = runner.invoke(
        app,
        ["quality", "validate-manifest", "--manifest", str(manifest)],
        env=_env(),
    )
    recommended = runner.invoke(
        app,
        [
            "quality",
            "recommend",
            "--manifest",
            str(manifest),
            "--report",
            str(report),
        ],
        env=_env(),
    )

    # Then both surfaces emit stable envelopes and only recommend promotion
    assert validated.exit_code == 0, validated.stdout
    assert json.loads(validated.stdout)["data"]["schema_version"] == "kip.quality-experiment.v1"
    assert recommended.exit_code == 0, recommended.stdout
    payload = json.loads(recommended.stdout)
    assert payload["data"]["status"] == "promote"


def test_quality_validation_does_not_initialize_optional_model_clients(tmp_path: Path) -> None:
    # Given a valid manifest and an unusable inherited SOCKS proxy
    manifest = ROOT / "evaluation/experiments/example.yaml"
    config = tmp_path / "models-enabled.toml"
    config.write_text(
        (ROOT / "config/kip.example.toml")
        .read_text(encoding="utf-8")
        .replace("enabled = false", "enabled = true"),
        encoding="utf-8",
    )
    environment = {**_env(), "ALL_PROXY": "socks5h://127.0.0.1:1"}

    # When the model-independent quality command runs
    result = CliRunner().invoke(
        app,
        [
            "--config",
            str(config),
            "quality",
            "validate-manifest",
            "--manifest",
            str(manifest),
        ],
        env=environment,
    )

    # Then optional embedding and reranker clients are not constructed
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["ok"] is True


def test_ontology_commands_validate_and_diff_releases(tmp_path: Path) -> None:
    # Given two identical valid ontology releases
    before = tmp_path / "before"
    after = tmp_path / "after"
    shutil.copytree(ROOT / "ontology", before)
    shutil.copytree(ROOT / "ontology", after)
    runner = CliRunner()

    # When the operator validates and compares them
    validated = runner.invoke(
        app,
        ["ontology", "validate", "--root", str(before)],
        env=_env(),
    )
    compared = runner.invoke(
        app,
        ["ontology", "diff", "--before", str(before), "--after", str(after)],
        env=_env(),
    )

    # Then compatibility is exposed through versioned JSON
    assert validated.exit_code == 0, validated.stdout
    assert json.loads(validated.stdout)["data"]["version"] == "core/1.0.0"
    assert compared.exit_code == 0, compared.stdout
    assert json.loads(compared.stdout)["data"]["classification"] == "compatible"
