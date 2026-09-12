import inspect
import json
import re
import shutil
from pathlib import Path

from typer.testing import CliRunner

from kip.cli import (
    _DEPLOYMENT_PANEL,
    _OPERATOR_PANEL,
    _RETRIEVAL_PANEL,
    _ROOT_HELP,
    app,
)

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


def test_sync_run_with_an_unknown_source_lists_configured_sources() -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["sync", "run", "--source", "does-not-exist"],
        env=_env(),
    )

    assert result.exit_code == 3, result.stdout
    payload = json.loads(result.stderr)
    assert payload["error"]["code"] == "validation_error"
    assert "unknown source: does-not-exist" in payload["error"]["message"]
    assert "Configured sources:" in payload["error"]["message"]
    assert "sample" in payload["error"]["message"]


def test_status_summary_flag_adds_a_plain_language_verdict_to_envelope_warnings() -> None:
    runner = CliRunner()

    plain = runner.invoke(app, ["status"], env=_env())
    assert plain.exit_code == 0, plain.stdout
    plain_payload = json.loads(plain.stdout)
    # `StatusReport` is a versioned contract model; the default `status`
    # output must stay unchanged (no field added to the model).
    assert "summary" not in plain_payload["data"]
    assert plain_payload["meta"]["warnings"] == []

    with_summary = runner.invoke(app, ["status", "--summary"], env=_env())
    assert with_summary.exit_code == 0, with_summary.stdout
    summary_payload = json.loads(with_summary.stdout)
    assert summary_payload["data"] == plain_payload["data"]
    warnings = summary_payload["meta"]["warnings"]
    assert len(warnings) == 1
    assert warnings[0].startswith("정상:") or warnings[0].startswith("문제:")


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
    assert payload["data"]["extensions"] == [".hwp", ".hwpx"]

    pdf = runner.invoke(
        app,
        ["parser", "reextract", "--source", "sample", "--extension", ".PDF"],
        env=_env(),
    )
    assert pdf.exit_code == 0, pdf.stdout
    assert json.loads(pdf.stdout)["data"]["extensions"] == [".pdf"]

    unknown = runner.invoke(
        app,
        ["parser", "reextract", "--source", "sample", "--extension", ".xyz"],
        env=_env(),
    )
    assert unknown.exit_code != 0
    assert "no parser is registered for .xyz" in unknown.output


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


def test_review_propose_defaults_ontology_version_to_the_active_catalog() -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "review",
            "propose",
            "--subject-id",
            "doc_new",
            "--predicate",
            "amends",
            "--object-entity-id",
            "doc_old",
        ],
        env=_env(),
    )

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["data"]["ontology_version"] == "core/1.0.0"


def test_review_propose_still_accepts_an_explicit_ontology_version() -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "review",
            "propose",
            "--subject-id",
            "doc_new",
            "--predicate",
            "amends",
            "--object-entity-id",
            "doc_old",
            "--ontology-version",
            "core/1.0.0",
        ],
        env=_env(),
    )

    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["data"]["ontology_version"] == "core/1.0.0"


def test_update_and_version_commands_work_without_a_database(tmp_path, monkeypatch) -> None:
    import subprocess

    from typer.testing import CliRunner

    from kip import __version__
    from kip.cli import app

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/upgrade.sh").write_text("#!/bin/sh\n")
    monkeypatch.setenv("KIP_PROJECT_ROOT", str(tmp_path))
    calls: list[list[str]] = []

    def fake_run(arguments, check=False):
        calls.append(list(arguments))
        return subprocess.CompletedProcess(arguments, 75)

    monkeypatch.setattr("kip.cli.subprocess.run", fake_run)
    runner = CliRunner()
    result = runner.invoke(app, ["update", "--version", "9.9.9", "--dry-run"])
    assert result.exit_code == 75
    assert calls[-1][1:] == ["--version", "9.9.9", "--dry-run"]
    assert calls[-1][0].endswith("scripts/upgrade.sh")
    assert runner.invoke(app, ["update", "--rollback"]).exit_code == 75
    assert calls[-1][1:] == ["--rollback"]
    assert runner.invoke(app, ["update"]).exit_code == 75
    assert calls[-1][1:] == ["--latest"]

    def plain(text: str) -> str:
        # Typer renders usage errors through rich: strip colour codes, box drawing and wrapping.
        return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).translate(str.maketrans("", "", "│╭╮╯╰─")).split())

    rejected = runner.invoke(app, ["update", "--rollback", "--dry-run"])
    assert rejected.exit_code != 0 and "cannot be combined" in plain(rejected.output)
    assert calls[-1][1:] == ["--latest"]  # nothing was executed for the rejected combination
    orphan = runner.invoke(app, ["update", "--rollback-id", "20260101T000000Z-1.0.0-to-2.0.0"])
    assert orphan.exit_code != 0 and "requires --rollback" in plain(orphan.output)
    assert runner.invoke(app, ["update", "--rollback", "--rollback-id", "abc"]).exit_code == 75
    assert calls[-1][1:] == ["--rollback", "abc"]
    assert runner.invoke(app, ["update", "--archive", "x.zip", "--no-bootstrap"]).exit_code == 75
    assert calls[-1][1:] == ["--archive", "x.zip", "--no-bootstrap"]

    version = runner.invoke(app, ["version"])
    assert version.exit_code == 0
    assert json.loads(version.output)["data"]["version"] == __version__


def _help(*command: str) -> str:
    """`--help` text with Rich's panel borders and line wrapping removed."""
    result = CliRunner().invoke(app, [*command, "--help"], env=_env())
    assert result.exit_code == 0, result.stdout
    unboxed = re.sub(r"[\u2500-\u257f]", " ", result.stdout)
    return re.sub(r"\s+", " ", unboxed)


def test_root_help_separates_read_only_retrieval_from_operator_commands() -> None:
    # An agent told that ordinary retrieval does not authorize sync, re-index
    # or projection rebuild gets no signal from one flat list of 30 commands.
    text = _help()

    assert "Retrieval (read-only)" in text
    assert "Operator (changes state" in text
    # The same split is spelled out in prose, because Typer drops the Rich
    # panels entirely when Rich is not installed.
    assert "Retrieval commands are read-only" in text
    assert "Operator commands change state and are not authorized by an ordinary retrieval request" in text
    for operator_command in ("sync", "projection", "rebuild", "migrate", "review", "ontology", "parser"):
        assert operator_command in text


# A leaf command that changes state. Matched against the command's own help
# text, so a new mutating command is caught by the same rule instead of
# needing to be remembered in a hand-kept list here.
_STATE_CHANGING_VERBS = re.compile(
    r"\b(delete|deletes|prune|prunes|rebuild|rebuilds|approve|approves|reject|rejects"
    r"|revoke|revokes|activate|activates|promote|promotes|migrate|migrates|write|writes"
    r"|remove|removes|create|creates|cancel|cancels|register|registers"
    r"|synchronize|synchronizes|sync|syncs)\b",
    re.IGNORECASE,
)


def _leaf_commands(typer_app, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    leaves = [
        ((*prefix, command.name or command.callback.__name__.replace("_", "-")),
         command.help or inspect.getdoc(command.callback) or "")
        for command in typer_app.registered_commands
    ]
    for group in typer_app.registered_groups:
        leaves.extend(_leaf_commands(group.typer_instance, (*prefix, group.name)))
    return leaves


def test_no_state_changing_command_is_listed_as_read_only_retrieval() -> None:
    # `telemetry` was grouped as read-only retrieval while `telemetry prune`
    # deleted query traces. The panels exist so an agent can tell a read-only
    # command from one that changes state, so a mutating leaf under the
    # retrieval panel defeats the whole grouping.
    panels = {
        command.name or command.callback.__name__.replace("_", "-"): command.rich_help_panel
        for command in app.registered_commands
    }
    panels.update({group.name: group.rich_help_panel for group in app.registered_groups})

    mutating = {
        path[0]
        for path, help_text in _leaf_commands(app)
        if _STATE_CHANGING_VERBS.search(help_text)
    }
    assert "telemetry" in mutating  # `telemetry prune` deletes query traces.

    retrieval_prose, operator_prose = _ROOT_HELP.split("Operator commands change state")
    for name in sorted(mutating):
        assert panels[name] != _RETRIEVAL_PANEL, name
        assert panels[name] == _OPERATOR_PANEL or panels[name] == _DEPLOYMENT_PANEL, name
        # Typer drops the Rich panels when Rich is absent, so the prose list
        # has to agree with the panel it is a fallback for.
        assert name not in retrieval_prose, name
        assert name in operator_prose, name


def test_search_help_lists_the_same_mode_values_the_mcp_schema_enumerates() -> None:
    # The CLI is the fallback surface when MCP is unavailable, so an allowed
    # value that only the MCP schema names is undiscoverable.
    for command in ("search", "context", "answer"):
        text = _help(command)
        assert "lexical | vector | hybrid | reranked" in text, command
        # `--source-kind` differs in shape from MCP's `source_kinds` array.
        assert "Repeat the option or pass a comma-separated list" in text, command
        assert "source_kinds" in text, command
        assert "document_types" in text, command
        assert "project_ids" in text, command

    assert "out | in | both" in _help("graph", "neighbors")


def test_allow_stale_help_says_what_it_relaxes_and_what_it_still_refuses() -> None:
    for command in (("xlsx-read",), ("xlsx", "read")):
        text = _help(*command)
        assert "Relax only the freshness guarantee" in text, command
        assert "still refuses when the workbook cannot be read" in text, command
        assert "ACL or source scope denies the artifact" in text, command
        assert "marks source_changed_since_index true" in text, command
        assert "keeps source_verification sha256" in text, command
