from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from kip.domain.egress import (
    DataClassification,
    EgressPolicy,
    EgressProvider,
    RetentionPolicy,
)
from kip.errors import ValidationError
from kip.setup.models import (
    FilesystemSourceAnswer,
    SecretReference,
    SetupAnswers,
)
from kip.setup.planner import build_setup_plan
from kip.setup.service import SetupService
from kip.setup.writer import apply_setup_plan
from tests.setup_support import prepare_setup_project

ROOT = Path(__file__).resolve().parents[1]


def test_verify_reports_runtime_readiness_without_failing_config_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path)
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    service = SetupService(project_root=project_root, state_path=state)
    plan = build_setup_plan(answers, project_root=project_root)
    apply_setup_plan(plan, project_root=project_root)
    monkeypatch.delenv("KIP_DATABASE_URL", raising=False)
    monkeypatch.delenv("KIP_DATABASE_URL_FILE", raising=False)

    receipt = service.verify(plan)

    # Configuration checks decide `verified`; environment gaps stay actionable.
    assert receipt.verified is True
    readiness = {check.name: check for check in receipt.runtime_readiness}
    assert readiness["python_version"].ok is True
    database = readiness["database_secret"]
    assert database.ok is False
    assert "KIP_DATABASE_URL" in database.detail
    source = readiness["source_readable:company-docs"]
    assert source.ok is True
    assert receipt.next_steps == [
        "./scripts/app-up.sh --database-only",
        "./scripts/kip sync run --source company-docs",
        './scripts/kip search "smoke test query" --limit 5',
        "./scripts/kip read UNIT_ID",
    ]
    assert any("configuration" in item for item in receipt.limitations)


@pytest.mark.parametrize(
    ("args", "config", "expected"),
    [
        (["scripts/mcp.sh"], "config/kip.host.generated.toml", True),
        (["scripts/mcp.sh"], "config/kip.toml", False),
        (["/elsewhere/scripts/mcp.sh"], "config/kip.host.generated.toml", False),
    ],
    ids=["pre-3.15.1-relative", "other-config", "other-deployment"],
)
def test_verify_accepts_the_relative_mcp_entry_upgrades_preserve(
    tmp_path: Path,
    args: list[str],
    config: str,
    expected: bool,
) -> None:
    # Given a deployment whose `.mcp.json` still holds the entry setup wrote
    # before 3.15.1, which an upgrade keeps.
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path)
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    plan = build_setup_plan(answers, project_root=project_root)
    apply_setup_plan(plan, project_root=project_root)
    entry = {"command": "bash", "args": args,
             "env": {"KIP_CONFIG": config, "KIP_WORKSPACE": plan.workspace}}
    (project_root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"kip": entry}}), encoding="utf-8"
    )

    # When setup verifies the applied plan.
    receipt = SetupService(project_root=project_root, state_path=state).verify(plan)

    # Then only an entry selecting this deployment's generated config passes.
    checks = {check.name: check.ok for check in receipt.checks}
    assert checks["mcp_runtime_config"] is expected


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"command": "bash", "args": ["{root}/scripts/mcp.sh"],
          "env": {"KIP_CONFIG": "{root}/config/kip.host.generated.toml", "KIP_WORKSPACE": "{workspace}"}}, True),
        ({"command": "bash", "args": ["/elsewhere/scripts/mcp.sh"],
          "env": {"KIP_CONFIG": "/elsewhere/config/kip.host.generated.toml", "KIP_WORKSPACE": "{workspace}"}}, False),
        ({"command": "kip", "args": ["mcp"]}, True),
        ({"command": "{root}/scripts/kip", "args": ["mcp"], "env": {"KIP_WORKSPACE": "{workspace}"}}, True),
        ({"command": "/srv/other-deployment/scripts/kip", "args": ["mcp"]}, False),
        ({"command": "kip", "args": ["mcp"], "env": {"KIP_PROJECT_ROOT": "/srv/other-deployment"}}, False),
        ({"command": "kip", "args": ["mcp"], "env": {"KIP_WORKSPACE": "another"}}, False),
        ({"command": "kip", "args": ["mcp"], "env": {"KIP_CONFIG": "/elsewhere/config/kip.toml"}}, False),
    ],
    ids=["absolute", "absolute-other-deployment", "launcher", "deployment-scripts-kip", "other-deployment-scripts-kip",
         "launcher-other-root", "launcher-other-workspace", "launcher-other-config"],
)
def test_verify_accepts_absolute_and_launcher_mcp_entries_for_this_deployment(
    tmp_path: Path,
    entry: dict[str, object],
    expected: bool,
) -> None:
    # Given an applied plan whose `.mcp.json` the operator replaced with the
    # absolute or launcher entry `kip doctor` suggests.
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path)
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    plan = build_setup_plan(answers, project_root=project_root)
    apply_setup_plan(plan, project_root=project_root)
    rendered = json.dumps(entry).replace("{root}", str(project_root.resolve())).replace(
        "{workspace}", plan.workspace
    )
    (project_root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"kip": json.loads(rendered)}}), encoding="utf-8"
    )

    # When setup verifies the applied plan.
    receipt = SetupService(project_root=project_root, state_path=state).verify(plan)

    # Then an entry serving this deployment passes and one naming another fails.
    checks = {check.name: check.ok for check in receipt.checks}
    assert checks["mcp_runtime_config"] is expected


def test_verify_resolves_database_secret_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path)
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    service = SetupService(project_root=project_root, state_path=state)
    plan = build_setup_plan(answers, project_root=project_root)
    apply_setup_plan(plan, project_root=project_root)
    monkeypatch.setenv("KIP_DATABASE_URL", "postgresql://example.invalid/kip")

    receipt = service.verify(plan)

    readiness = {check.name: check for check in receipt.runtime_readiness}
    assert readiness["database_secret"].ok is True


def test_env_only_secret_questions_reject_file_references(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    answers = SetupAnswers(workspace="acme-rnd", identity_mode="api_key")
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    service = SetupService(project_root=tmp_path / "project", state_path=state)

    with pytest.raises(ValidationError, match="env: reference"):
        service.record_answer(
            "identity_api_key_secret_ref",
            "file:/run/secrets/kip-api-key",
        )


def test_database_secret_question_rejects_non_env_schemes(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path).model_copy(
        update={"database_secret_ref": None}
    )
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    service = SetupService(project_root=tmp_path / "project", state_path=state)

    with pytest.raises(ValidationError, match="env: reference"):
        service.record_answer("database_secret_ref", "file:/run/secrets/db-url")
    with pytest.raises(ValidationError, match="env:NAME or file:"):
        service.record_answer("database_secret_ref", "keychain:kip/database")


def test_model_secret_question_accepts_file_reference(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path).model_copy(
        update={"model_secret_ref": None}
    )
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    service = SetupService(project_root=tmp_path / "project", state_path=state)

    inspection = service.record_answer(
        "model_secret_ref",
        "file:/run/secrets/kip-model-key",
    )

    restored = service.load_answers()
    assert restored.model_secret_ref is not None
    assert restored.model_secret_ref.display() == "file:/run/secrets/kip-model-key"
    assert inspection.complete is True


def test_egress_gate_matches_runtime_resolvable_secret_schemes() -> None:
    def policy(secret_reference: str) -> EgressPolicy:
        return EgressPolicy(
            enabled=True,
            provider=EgressProvider.OPENAI,
            allow_remote=True,
            allowed_classifications=(DataClassification.PUBLIC,),
            retention_policy=RetentionPolicy.ZERO_RETENTION,
            secret_reference=secret_reference,
        )

    from kip.domain.egress import ClassifiedEvidence, evaluate_egress

    evidence = [
        ClassifiedEvidence(
            id="unit_1",
            classification=DataClassification.PUBLIC,
        )
    ]
    assert evaluate_egress(policy("env:KIP_OPENAI_API_KEY"), evidence).allowed
    assert evaluate_egress(policy("file:/run/secrets/model-key"), evidence).allowed
    assert not evaluate_egress(policy("keychain:kip/openai"), evidence).allowed


def _applied_project(tmp_path: Path) -> tuple[Path, SetupService, object]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    state = tmp_path / "state.json"
    answers = _complete_answers(tmp_path)
    state.write_text(answers.model_dump_json(), encoding="utf-8")
    plan = build_setup_plan(answers, project_root=project_root)
    apply_setup_plan(plan, project_root=project_root)
    return project_root, SetupService(project_root=project_root, state_path=state), plan


def _stub_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, ps_output: str, ps_exit: int = 0,
    volumes_output: str = "", listening: set[int],
) -> None:
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "if sys.argv[1:2] == ['ps']:\n"
        f"    sys.stdout.write({ps_output!r})\n"
        f"    raise SystemExit({ps_exit})\n"
        "if sys.argv[1:3] == ['volume', 'ls']:\n"
        f"    sys.stdout.write({volumes_output!r})\n"
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr("kip.setup.service._port_listening", lambda port: port in listening)
    for name in ("COMPOSE_PROJECT_NAME", "KIP_POSTGRES_PORT", "KIP_API_PORT"):
        monkeypatch.delenv(name, raising=False)


def _isolation(receipt: object) -> object:
    [check] = [item for item in receipt.runtime_readiness if item.name == "compose_project_isolation"]
    return check


def test_verify_fails_readiness_when_another_deployment_owns_the_project_and_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the running first deployment's containers under the default project.
    project_root, service, plan = _applied_project(tmp_path)
    other = tmp_path / "first-deployment"
    other.mkdir()
    _stub_docker(tmp_path, monkeypatch, listening={5432}, ps_output=(
        f"kip\t{other}\t127.0.0.1:5432->5432/tcp\n"
        f"kip\t{other}\t\n"
        "\t\t0.0.0.0:7997->7997/tcp\n"
    ), volumes_output="kip\tkip_kip_pgdata\n")

    receipt = service.verify(plan)

    check = _isolation(receipt)
    assert check.ok is False
    root = project_root.resolve()
    assert check.detail == (
        f'Docker Compose project "kip" already has containers from {other}; '
        f'127.0.0.1:5432 (KIP_POSTGRES_PORT) is held by a container of Compose project "kip" at {other}. '
        f"Before ./scripts/app-up.sh: set a unique COMPOSE_PROJECT_NAME in {root / '.env'} for a new, "
        "empty database, or, if this is the same deployment moved from another directory, run "
        "KIP_COMPOSE_ADOPT=1 ./scripts/app-up.sh --down once; set a free KIP_POSTGRES_PORT and the "
        "same port in KIP_DATABASE_URL and KIP_BACKUP_DATABASE_URL. Leave KIP_SEMANTIC_PORT on the "
        "running model runtime (docs/OPERATIONS.md)"
    )
    # Runtime readiness never decides the configuration verdict.
    assert receipt.verified is True


def test_verify_reads_env_like_common_sh_and_scopes_the_api_port_to_the_app_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a .env with the `export`, quoting and inline comments common.sh accepts.
    project_root, service, plan = _applied_project(tmp_path)
    (project_root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/load_dotenv.py", project_root / "scripts/load_dotenv.py")
    (project_root / ".env").write_text(
        "# second deployment\n"
        "export COMPOSE_PROJECT_NAME=kip-second # chosen by bootstrap\n"
        "KIP_POSTGRES_PORT='55433'\n"
        'KIP_API_PORT="18081"\n',
        encoding="utf-8",
    )
    other = tmp_path / "first-deployment"
    other.mkdir()
    # The first deployment's project, volumes and ports are not this one's concern.
    _stub_docker(tmp_path, monkeypatch, listening={5432, 8080, 18081}, ps_output=(
        f"kip\t{other}\t127.0.0.1:5432->5432/tcp\n"
    ), volumes_output="kip\tkip_kip_pgdata\n")

    check = _isolation(service.verify(plan))

    assert check.ok is False
    assert check.detail == (
        "127.0.0.1:18081 (KIP_API_PORT) is held by a process that is not this deployment's "
        "container. Before ./scripts/app-up.sh: only the app profile publishes the API "
        "(./scripts/app-up.sh without --database-only), so before starting it set a free "
        "KIP_API_PORT. Leave KIP_SEMANTIC_PORT on the running model runtime (docs/OPERATIONS.md)"
    )


def test_verify_passes_readiness_for_this_deployments_own_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, service, plan = _applied_project(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(project_root, target_is_directory=True)
    _stub_docker(tmp_path, monkeypatch, listening={5432}, ps_output=(
        f"kip\t{alias}\t127.0.0.1:5432->5432/tcp\n"
        "other\t/srv/other\t127.0.0.1:18080-18090->8080-8090/tcp\n"
    ), volumes_output="kip\tkip_kip_pgdata\nkip\tkip_kip_cas\n")

    check = _isolation(service.verify(plan))

    assert check.ok is True
    assert check.detail == 'Compose project "kip" and 127.0.0.1:5432/8080 belong to this deployment or are free'


def test_verify_fails_readiness_for_project_volumes_it_cannot_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a first deployment stopped with `app-up.sh --down`: volumes, no containers.
    _, service, plan = _applied_project(tmp_path)
    _stub_docker(tmp_path, monkeypatch, listening=set(), ps_output="",
                 volumes_output="kip\tkip_kip_pgdata\nkip\tkip_kip_cas\nother\tother_data\n")

    check = _isolation(service.verify(plan))

    assert check.ok is False
    assert check.detail.startswith(
        'Docker Compose project "kip" has volumes (kip_kip_cas, kip_kip_pgdata) but no containers, '
        "and Docker does not record which directory created them; unless this deployment created "
        "them and was stopped with ./scripts/app-up.sh --down, they hold another deployment's "
        "database, which this deployment's password cannot open. Before ./scripts/app-up.sh: set "
        "a unique COMPOSE_PROJECT_NAME"
    )


def test_verify_fails_readiness_for_a_project_name_it_cannot_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, service, plan = _applied_project(tmp_path)
    generated = project_root / "compose.generated.yaml"
    generated.write_text(
        re.sub(r"^name: kip$", "name: ${STACK_NAME:-kip}", generated.read_text(), flags=re.MULTILINE)
    )
    _stub_docker(tmp_path, monkeypatch, listening=set(), ps_output="")

    check = _isolation(service.verify(plan))

    assert check.ok is False
    assert check.detail == (
        "compose.generated.yaml sets the Compose project name by interpolation "
        "(name: ${STACK_NAME:-kip}), which setup verify does not resolve; set "
        f"COMPOSE_PROJECT_NAME in {project_root.resolve() / '.env'} to the resolved name"
    )


def test_verify_marks_isolation_not_checked_when_docker_cannot_be_queried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, service, plan = _applied_project(tmp_path)
    _stub_docker(tmp_path, monkeypatch, listening={5432, 8080}, ps_output="", ps_exit=1)

    check = _isolation(service.verify(plan))

    assert (check.ok, check.detail) == (True, "not checked: Docker could not be queried")


def _complete_answers(tmp_path: Path) -> SetupAnswers:
    prepare_setup_project(tmp_path / "project")
    source = tmp_path / "company-docs"
    source.mkdir(exist_ok=True)
    backup = tmp_path / "backup"
    backup.mkdir(exist_ok=True)
    return SetupAnswers(
        workspace="acme-rnd",
        identity_mode="proxy_jwt",
        jwt_issuer="https://identity.example.test/",
        jwt_audience="kip-api",
        jwt_jwks_url="https://identity.example.test/.well-known/jwks.json",
        jwt_admin_groups=["kip-admins"],
        identity_owner="platform-security",
        source_ownership="company",
        ontology_profile="empty",
        filesystem_sources=[
            FilesystemSourceAnswer.from_user_value(
                {
                    "name": "company-docs",
                    "root": str(source),
                    "classification": "internal",
                    "acl_scope": "workspace:acme-rnd",
                },
                project_root=tmp_path / "project",
            )
        ],
        model_provider="openai",
        model_egress_classifications=["public", "internal"],
        model_retention_policy="zero_retention",
        model_secret_ref=SecretReference.parse("env:KIP_OPENAI_API_KEY"),
        relation_mining_mode="enabled",
        database_secret_ref=SecretReference.parse("env:KIP_DATABASE_URL"),
        cas_path=str((tmp_path / "cas").resolve()),
        backup_path=str(backup.resolve()),
        retention_days=365,
        sync_schedule="0 * * * *",
        evaluation_dataset="none",
        interaction_memory_mode="explicit_consent",
        ontology_reviewers=["knowledge-owner@example.invalid"],
    )
