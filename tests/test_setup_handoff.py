from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import pytest
import yaml

from kip.adapters.connectors.registry import ConfiguredSourceCatalog
from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.models import SearchRequest
from kip.errors import ConfigurationError, ConflictError, ValidationError
from kip.settings import Settings
from kip.setup.models import SecretReference
from kip.setup.paths import validate_container_source_target
from kip.setup.planner import build_setup_plan
from kip.setup.service import SetupService
from kip.setup.writer import apply_setup_plan
from tests.setup_support import complete_setup_answers

ROOT = Path(__file__).resolve().parents[1]


def test_external_database_readiness_does_not_probe_docker(tmp_path, monkeypatch):
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path).model_copy(update={
        "database_secret_ref": SecretReference.parse("env:EXTERNAL_DATABASE_URL"),
    })
    plan = build_setup_plan(answers, project_root=project)
    monkeypatch.setenv("EXTERNAL_DATABASE_URL", "postgresql://external.example.test/kip")
    monkeypatch.setattr("kip.setup.service.shutil.which", lambda name: pytest.fail("external DB does not need Docker discovery"))
    service = SetupService(project_root=project, state_path=project / ".kip/setup.json")
    checks = {check.name: check for check in service._runtime_readiness(plan)}
    assert checks["docker_cli"].ok
    assert "not required" in checks["docker_cli"].detail
    assert "docker_daemon" not in checks


def _database_url(host: str, password: str) -> str:
    credentials = "kip_owner:" + quote(password, safe="")
    return urlunsplit(("postgresql", credentials + "@" + host, "/kip", "", ""))


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "scripts").mkdir()
    for name in ("common.sh", "load_dotenv.py"):
        shutil.copy2(ROOT / "scripts" / name, project / "scripts" / name)
    shutil.copy2(ROOT / "compose.yaml", project / "compose.yaml")
    shutil.copy2(ROOT / ".env.example", project / ".env")
    return project


def _clean_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("KIP_")}


def test_common_selects_approved_host_settings_after_apply(tmp_path: Path) -> None:
    project = _project(tmp_path)
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project)
    apply_setup_plan(plan, project_root=project)
    result = subprocess.run(
        ["bash", "-c", 'source "$1/scripts/common.sh"; "$2" -c "$3"', "handoff",
         str(project), sys.executable,
         "from kip.settings import Settings; import json,os; s=Settings.load(); "
         "print(json.dumps([s.config_path.name,s.workspace,s.identity_mode,"
         "s.jwt_issuer,str(s.cas_path),os.environ.get('KIP_ACL_SCOPES')]))"],
        cwd=project, env={**_clean_environment(), "PYTHONPATH": str(ROOT / "src")},
        capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == [
        "kip.host.generated.toml", "acme-rnd", "proxy_jwt",
        "https://identity.example.test/", plan.cas_path, None,
    ]


def test_common_preserves_explicit_config_and_environment(tmp_path: Path) -> None:
    project = _project(tmp_path)
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project)
    apply_setup_plan(plan, project_root=project)
    result = subprocess.run(
        ["bash", "-c", 'source "$1/scripts/common.sh"; printf "%s|%s" "$KIP_CONFIG" "$KIP_WORKSPACE"',
         "handoff", str(project)],
        cwd=project,
        env={**_clean_environment(), "KIP_CONFIG": "config/custom.toml", "KIP_WORKSPACE": "explicit"},
        capture_output=True, text=True, check=True,
    )
    assert result.stdout == "config/custom.toml|explicit"


def test_custom_secret_reference_wins_over_bootstrap_defaults(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path).model_copy(update={
        "identity_mode": "api_key",
        "identity_api_key_secret_ref": SecretReference.parse("env:ACME_API_KEY"),
        "identity_admin_key_secret_ref": SecretReference.parse("env:ACME_ADMIN_KEY"),
        "database_secret_ref": SecretReference.parse("env:ACME_DATABASE_URL"),
    })
    apply_setup_plan(build_setup_plan(answers, project_root=project), project_root=project)
    monkeypatch.setenv("KIP_PROJECT_ROOT", str(project))
    for key, value in {"KIP_DATABASE_URL": "memory://", "KIP_API_KEY": "old-key", "KIP_ADMIN_KEY": "old-admin",
                       "ACME_DATABASE_URL": "postgresql://acme.invalid/kip", "ACME_API_KEY": "selected-key",
                       "ACME_ADMIN_KEY": "selected-admin"}.items():
        monkeypatch.setenv(key, value)
        monkeypatch.delenv(f"{key}_FILE", raising=False)
    settings = Settings.load(project / "config/kip.host.generated.toml")
    assert (settings.database_url, settings.api_key, settings.admin_key) == (
        "postgresql://acme.invalid/kip", "selected-key", "selected-admin",
    )


def test_generated_compose_has_one_database_and_only_approved_sources(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI unavailable")
    project = _project(tmp_path)
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project)
    apply_setup_plan(plan, project_root=project)
    owner_url = _database_url("postgres:5432", secrets.token_urlsafe(24))
    api_password = secrets.token_urlsafe(24)
    worker_password = secrets.token_urlsafe(24)
    result = subprocess.run(
        ["docker", "compose", "-f", "compose.generated.yaml", "--profile", "app", "config", "--format", "json"],
        cwd=project, env={
            **_clean_environment(), "KIP_OPENAI_API_KEY": "synthetic-key",
            "KIP_CONTAINER_DATABASE_URL": owner_url,
            "KIP_API_DB_PASSWORD": api_password, "KIP_WORKER_DB_PASSWORD": worker_password,
            "KIP_BACKUP_DB_PASSWORD": secrets.token_urlsafe(24),
        },
        capture_output=True, text=True, check=True,
    )
    services = json.loads(result.stdout)["services"]
    assert "postgres" in services
    # One database, three logins: only migrate keeps the owner URL, and the api
    # and worker use the non-superuser roles the `roles` service creates.
    assert services["migrate"]["environment"]["KIP_DATABASE_URL"] == owner_url
    for name, role, password in (
        ("api", "kip_api", api_password), ("worker", "kip_worker", worker_password),
    ):
        selected = urlsplit(services[name]["environment"]["KIP_DATABASE_URL"])
        assert (selected.username, unquote(selected.password or "")) == (role, password)
        assert (selected.hostname, selected.port, selected.path) == ("postgres", 5432, "/kip")
    for name in ("api", "worker", "migrate"):
        environment = services[name]["environment"]
        assert "@postgres:5432/" in environment["KIP_DATABASE_URL"]
        assert environment["KIP_CONFIG"] == "/app/config/kip.generated.toml"
        assert environment["KIP_IDENTITY_MODE"] == "proxy_jwt"
        assert environment["KIP_JWT_ISSUER"] == plan.jwt_issuer
        assert services[name]["user"] == f"{os.getuid()}:{os.getgid()}"
        assert services[name].get("group_add", []) == [str(group) for group in plan.runtime_supplementary_gids]
        targets = {mount["target"] for mount in services[name]["volumes"]}
        assert "/sources/nas" not in targets
        assert "/var/lib/kip/cas" in targets
        if name != "migrate":
            assert plan.sources[0].host_root in targets
            source_mount = next(mount for mount in services[name]["volumes"] if mount["target"] == plan.sources[0].host_root)
            assert source_mount["source"] == source_mount["target"]
            assert source_mount["read_only"] is True
        else:
            assert plan.sources[0].host_root not in targets
            cas_mount = next(mount for mount in services[name]["volumes"] if mount["target"] == "/var/lib/kip/cas")
            assert cas_mount["source"] == plan.cas_path
            assert not cas_mount.get("read_only", False)


def test_generated_compose_never_gives_api_or_worker_the_owner_url(tmp_path: Path) -> None:
    project = _project(tmp_path)
    apply_setup_plan(
        build_setup_plan(complete_setup_answers(tmp_path), project_root=project),
        project_root=project,
    )

    services = yaml.safe_load(
        (project / "compose.generated.yaml").read_text(encoding="utf-8")
    )["services"]
    # KIP_CONTAINER_DATABASE_URL is the bootstrap owner, which PostgreSQL
    # creates SUPERUSER with BYPASSRLS: a superuser bypasses every workspace and
    # ACL policy, so only migrate and the one-shot `roles` service may use it.
    assert services["migrate"]["environment"]["KIP_DATABASE_URL"] == (
        "${KIP_CONTAINER_DATABASE_URL:?start the generated profile with ./scripts/app-up.sh}"
    )
    for name, role, password in (
        ("api", "kip_api", "KIP_API_DB_PASSWORD"),
        ("worker", "kip_worker", "KIP_WORKER_DB_PASSWORD"),
    ):
        url = services[name]["environment"]["KIP_DATABASE_URL"]
        assert url.startswith(f"postgresql://{role}:${{{password}:?")
        assert "KIP_CONTAINER_DATABASE_URL" not in url
        assert "POSTGRES_USER" not in url and "POSTGRES_PASSWORD" not in url
        assert services[name]["depends_on"]["roles"] == {
            "condition": "service_completed_successfully"
        }
    assert services["roles"]["depends_on"]["migrate"] == {
        "condition": "service_completed_successfully"
    }
    assert services["roles"]["environment"]["PGUSER"] == "${POSTGRES_USER:-kip_owner}"
    assert services["roles"]["volumes"] == [{
        "type": "bind", "source": "./deploy", "target": "/deploy",
        "read_only": True, "bind": {"create_host_path": False},
    }]


def test_generated_compose_for_external_database_leaves_role_creation_to_the_operator(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path).model_copy(update={
        "database_secret_ref": SecretReference.parse("env:ACME_DATABASE_URL"),
    })
    apply_setup_plan(build_setup_plan(answers, project_root=project), project_root=project)

    compose = yaml.safe_load((project / "compose.generated.yaml").read_text(encoding="utf-8"))
    # KIP neither owns nor can create roles in a database it did not start, so
    # the one-shot service is left out and the file says who must do it.
    assert "roles" not in compose["services"]
    assert "postgres" not in compose["services"]
    note = compose["x-kip-database-roles"]
    assert "deploy/sql/roles.sql.template" in note
    assert "env:ACME_DATABASE_URL" in note
    assert "BYPASSRLS" in note
    for name in ("api", "worker", "migrate"):
        environment = compose["services"][name]["environment"]
        assert environment["ACME_DATABASE_URL"] == "${ACME_DATABASE_URL:?required}"
        assert "KIP_DATABASE_URL" not in environment


def test_readiness_rejects_unreadable_database_secret_file(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path)
    plan = build_setup_plan(answers, project_root=project)
    apply_setup_plan(plan, project_root=project)
    monkeypatch.delenv("KIP_DATABASE_URL", raising=False)
    monkeypatch.setenv("KIP_DATABASE_URL_FILE", str(tmp_path / "missing-secret"))
    receipt = SetupService(project_root=project, state_path=tmp_path / "state.json").verify(plan)
    assert not next(check for check in receipt.runtime_readiness if check.name == "database_secret").ok
    assert receipt.next_steps[0] == "./scripts/app-up.sh --database-only"


def test_bootstrap_env_creates_random_secrets_without_replacing_existing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / ".env").unlink()
    shutil.copy2(ROOT / ".env.example", project / ".env.example")
    script = ROOT / "scripts/bootstrap_env.py"
    subprocess.run([sys.executable, str(script), str(project)], check=True, capture_output=True)
    first = (project / ".env").read_bytes()
    assert b"change-me-before-use" not in first
    assert b"replace-with-a-long-random-secret" not in first
    assert (project / ".env").stat().st_mode & 0o777 == 0o600
    subprocess.run([sys.executable, str(script), str(project)], check=True, capture_output=True)
    assert (project / ".env").read_bytes() == first


def test_plan_refuses_root_and_legacy_ownership_at_apply(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path)
    plan = build_setup_plan(answers, project_root=project)
    legacy = plan.model_copy(update={"runtime_uid": None, "runtime_gid": None})
    legacy = legacy.model_copy(update={"plan_fingerprint": legacy.calculate_fingerprint()})
    legacy.verify_fingerprint()
    with pytest.raises(ConflictError, match="regenerate"):
        apply_setup_plan(legacy, project_root=project)
    monkeypatch.setattr(os, "getuid", lambda: 0)
    with pytest.raises(ValidationError, match="non-root"):
        build_setup_plan(answers, project_root=project)


def test_plan_groups_are_fingerprinted_and_apply_cannot_grant_new_membership(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path)
    monkeypatch.setattr(os, "getgroups", lambda: [os.getgid(), 90210, 90210, 90211])
    plan = build_setup_plan(answers, project_root=project)
    assert plan.runtime_supplementary_gids == [90210, 90211]
    apply_setup_plan(plan, project_root=project)
    monkeypatch.setattr(os, "getgroups", lambda: [os.getgid(), 90210])
    with pytest.raises(ConflictError, match="supplementary groups"):
        apply_setup_plan(plan, project_root=project)
    legacy = plan.model_copy(update={"runtime_supplementary_gids": None})
    legacy = legacy.model_copy(update={"plan_fingerprint": legacy.calculate_fingerprint()})
    legacy.verify_fingerprint()
    with pytest.raises(ConflictError, match="regenerate"):
        apply_setup_plan(legacy, project_root=project)


@pytest.mark.parametrize("custom_reference", [True, False])
def test_missing_production_database_never_falls_back_to_memory(tmp_path: Path, monkeypatch, custom_reference: bool) -> None:
    config = tmp_path / "production.toml"
    name = "ACME_DATABASE_URL" if custom_reference else "KIP_DATABASE_URL"
    config.write_text(f'[app]\nenvironment="production"\n[database]\nurl_env="{name}"\n')
    monkeypatch.setenv("KIP_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("KIP_ENV", "production")
    monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(f"{name}_FILE", raising=False)
    with pytest.raises(ConfigurationError, match=name):
        Settings.load(config)
    monkeypatch.setenv("KIP_ENV", "test")
    monkeypatch.setenv(name, "memory://")
    assert Settings.load(config).database_url == "memory://"


def test_verify_rejects_runtime_owner_tampering_even_with_original_fingerprint(tmp_path: Path) -> None:
    project = _project(tmp_path)
    plan = build_setup_plan(complete_setup_answers(tmp_path), project_root=project)
    apply_setup_plan(plan, project_root=project)
    path = project / "compose.generated.yaml"
    compose = yaml.safe_load(path.read_text())
    compose["services"]["api"]["user"] = "0:0"
    path.write_text(yaml.safe_dump(compose))
    receipt = SetupService(project_root=project, state_path=tmp_path / "state.json").verify(plan)
    assert not receipt.verified


def test_path_answers_have_safe_visible_defaults_and_stable_names(tmp_path: Path) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path)
    state = tmp_path / "state.json"
    state.write_text(answers.model_dump_json())
    service = SetupService(project_root=project, state_path=state)
    folder = tmp_path / "한국어 OneDrive"
    folder.mkdir()
    service.record_answer("filesystem_sources", str(folder))
    first = service.load_answers().filesystem_sources[0]
    assert first.acl_scope == "workspace:acme-rnd"
    assert first.classification == "restricted"
    assert first.root == str(folder.resolve())
    service.record_answer("filesystem_sources", json.dumps([str(folder)]))
    assert service.load_answers().filesystem_sources[0].name == first.name
    linked = tmp_path / "link"
    linked.symlink_to(folder)
    with pytest.raises(ValueError, match="symlink"):
        service.record_answer("filesystem_sources", str(linked))


def test_external_database_is_used_by_every_service_without_bundled_postgres(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI unavailable")
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path).model_copy(update={
        "database_secret_ref": SecretReference.parse("env:ACME_DATABASE_URL"),
        "identity_mode": "api_key",
        "identity_api_key_secret_ref": SecretReference.parse("env:ACME_API_KEY"),
        "identity_admin_key_secret_ref": SecretReference.parse("env:ACME_ADMIN_KEY"),
        "model_provider": "disabled", "model_secret_ref": None, "relation_mining_mode": "disabled",
    })
    apply_setup_plan(build_setup_plan(answers, project_root=project), project_root=project)
    selected_url = "postgresql://selected.invalid/kip"
    result = subprocess.run(
        ["docker", "compose", "-f", "compose.generated.yaml", "--profile", "app", "config", "--format", "json"],
        cwd=project, capture_output=True, text=True, check=True,
        env={**_clean_environment(), "ACME_DATABASE_URL": selected_url, "ACME_API_KEY": "synthetic-key", "ACME_ADMIN_KEY": "synthetic-admin"},
    )
    services = json.loads(result.stdout)["services"]
    assert "postgres" not in services
    # No bundled database, so no `roles` service either: every remaining service
    # uses the one external URL the operator approved (see
    # test_generated_compose_for_external_database_leaves_role_creation_to_the_operator).
    assert "roles" not in services
    for service in services.values():
        assert service["environment"]["ACME_DATABASE_URL"] == selected_url
        assert "KIP_DATABASE_URL" not in service["environment"]
        assert "KIP_API_KEY" not in service["environment"]
        assert "postgres" not in service.get("depends_on", {})


def test_compose_launcher_resolves_file_secrets_without_mounting_host_secret_path(tmp_path: Path) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path).model_copy(update={
        "database_secret_ref": SecretReference.parse("env:ACME_DATABASE_URL"),
        "model_provider": "disabled", "model_secret_ref": None, "relation_mining_mode": "disabled",
    })
    apply_setup_plan(build_setup_plan(answers, project_root=project), project_root=project)
    secret = tmp_path / "db-secret"
    secret.write_text("postgresql://synthetic.invalid/kip\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport json,os,sys\n"
        "print(json.dumps([sys.argv[1:],os.environ.get('ACME_DATABASE_URL'),os.environ.get('ACME_DATABASE_URL_FILE')]))\n"
    )
    docker.chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/setup_compose.py"), "config"],
        env={**_clean_environment(), "PROJECT_ROOT": str(project), "PYTHONPATH": str(ROOT / "src"),
             "PATH": f"{binary}:{os.environ['PATH']}", "ACME_DATABASE_URL_FILE": str(secret)},
        capture_output=True, text=True, check=True,
    )
    arguments, value, file_value = json.loads(result.stdout)
    assert arguments == ["compose", "-f", "compose.generated.yaml", "--profile", "app", "config"]
    assert value == "postgresql://synthetic.invalid/kip"
    assert file_value is None
    secret.write_text("first\nsecond\n")
    failed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/setup_compose.py"), "up"],
        env={**_clean_environment(), "PROJECT_ROOT": str(project), "PYTHONPATH": str(ROOT / "src"),
             "PATH": f"{binary}:{os.environ['PATH']}", "ACME_DATABASE_URL_FILE": str(secret)},
        capture_output=True, text=True,
    )
    assert failed.returncode == 1
    assert not failed.stdout
    assert "first" not in failed.stderr


def test_compose_launcher_refuses_external_url_in_bundled_reference(tmp_path: Path) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path)
    apply_setup_plan(build_setup_plan(answers, project_root=project), project_root=project)
    password = secrets.token_urlsafe(24)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/setup_compose.py"), "up"],
        env={**_clean_environment(), "PROJECT_ROOT": str(project), "PYTHONPATH": str(ROOT / "src"),
             "KIP_DATABASE_URL": _database_url("external.invalid", password)},
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "custom database secret reference" in result.stderr
    assert password not in result.stderr


def test_compose_launcher_encodes_managed_password_for_container_url(tmp_path: Path) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path).model_copy(update={
        "model_provider": "disabled", "model_secret_ref": None, "relation_mining_mode": "disabled",
    })
    apply_setup_plan(build_setup_plan(answers, project_root=project), project_root=project)
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport json,os\n"
        "print(json.dumps([os.environ['KIP_CONTAINER_DATABASE_URL'],os.environ['POSTGRES_PASSWORD']]))\n"
    )
    docker.chmod(0o755)
    password = secrets.token_urlsafe(24) + "/@:#?"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/setup_compose.py"), "config"],
        env={**_clean_environment(), "PROJECT_ROOT": str(project), "PYTHONPATH": str(ROOT / "src"),
             "PATH": f"{binary}:{os.environ['PATH']}", "POSTGRES_PASSWORD": password,
             "KIP_DATABASE_URL": _database_url("127.0.0.1:5432", password)},
        capture_output=True, text=True, check=True,
    )
    container_url, postgres_password = json.loads(result.stdout)
    parsed = urlsplit(container_url)
    assert parsed.hostname == "postgres"
    assert parsed.port == 5432
    assert unquote(parsed.password) == postgres_password == password
    assert parsed.path == "/kip"
    assert not parsed.query and not parsed.fragment
    assert password not in (project / "compose.generated.yaml").read_text()


def test_readiness_rejects_placeholder_identity_keys_and_reports_separation(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path).model_copy(update={
        "identity_mode": "api_key",
        "identity_api_key_secret_ref": SecretReference.parse("env:ACME_API_KEY"),
        "identity_admin_key_secret_ref": SecretReference.parse("env:ACME_ADMIN_KEY"),
    })
    plan = build_setup_plan(answers, project_root=project)
    apply_setup_plan(plan, project_root=project)
    monkeypatch.setenv("ACME_API_KEY", "replace-with-synthetic-secret")
    monkeypatch.setenv("ACME_ADMIN_KEY", "replace-with-synthetic-secret")
    receipt = SetupService(project_root=project, state_path=tmp_path / "state.json").verify(plan)
    readiness = {check.name: check for check in receipt.runtime_readiness}
    assert not readiness["identity_api_key_secret"].ok
    assert not readiness["identity_admin_key_secret"].ok
    assert not readiness["identity_key_separation"].ok
    assert "replace-with-synthetic-secret" not in receipt.model_dump_json()


@pytest.mark.parametrize("local_count", [0, 1])
def test_preview_counts_cloud_only_files_without_opening_them(tmp_path: Path, monkeypatch, local_count: int) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path)
    source_root = Path(answers.filesystem_sources[0].root)
    (source_root / "cloud.txt").write_bytes(b"cloud-content")
    if local_count:
        (source_root / "local.txt").write_bytes(b"local")
    monkeypatch.setattr("kip.setup.inventory.is_cloud_placeholder", lambda info: info.st_size == 13)
    original_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        if path.is_relative_to(source_root):
            raise AssertionError("inventory must not open source content")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    state = tmp_path / "state.json"
    state.write_text(answers.model_dump_json())
    service = SetupService(project_root=project, state_path=state)
    preview = service.preview()[0]
    assert preview.file_count == 1 + local_count
    assert preview.local_file_count == local_count
    assert preview.cloud_placeholder_count == 1
    plan = service.create_plan()
    assert any("cloud-only" in warning for warning in plan.warnings)
    apply_setup_plan(plan, project_root=project)
    receipt = service.verify(plan)
    check = next(item for item in receipt.runtime_readiness if item.name == "source_local_files:company-docs")
    assert check.ok is bool(local_count)
    assert "download chosen files" in check.detail


@pytest.mark.parametrize("external_database", [False, True])
def test_down_parses_compose_without_resolving_unavailable_credentials(
    tmp_path: Path, external_database: bool,
) -> None:
    real_docker = shutil.which("docker")
    if real_docker is None:
        pytest.skip("Docker Compose CLI unavailable")
    project = _project(tmp_path)
    (project / ".env").unlink()
    database_name = "TEARDOWN_DATABASE_URL" if external_database else "KIP_DATABASE_URL"
    answers = complete_setup_answers(tmp_path).model_copy(update={
        "database_secret_ref": SecretReference.parse(f"env:{database_name}"),
        "identity_mode": "api_key",
        "identity_api_key_secret_ref": SecretReference.parse("env:TEARDOWN_API_KEY"),
        "identity_admin_key_secret_ref": SecretReference.parse("env:TEARDOWN_ADMIN_KEY"),
        "model_secret_ref": SecretReference.parse("env:TEARDOWN_MODEL_KEY"),
    })
    apply_setup_plan(build_setup_plan(answers, project_root=project), project_root=project)
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport os,subprocess,sys\n"
        "assert sys.argv[-1]=='down', 'test must never start or stop a real service'\n"
        "result=subprocess.run([os.environ['TEARDOWN_REAL_DOCKER'],*sys.argv[1:-1],'config','--format','json'])\n"
        "raise SystemExit(result.returncode)\n"
    )
    docker.chmod(0o755)
    cached_key = secrets.token_urlsafe(24)
    environment = {
        key: value for key, value in _clean_environment().items()
        if not key.startswith(("POSTGRES_", "TEARDOWN_"))
    }
    environment.update({
        "PROJECT_ROOT": str(project), "PYTHONPATH": str(ROOT / "src"),
        "PATH": f"{binary}:{os.environ['PATH']}", "TEARDOWN_REAL_DOCKER": real_docker,
        "TEARDOWN_API_KEY": cached_key,
        f"{database_name}_FILE": str(tmp_path / "missing-database-secret"),
        "TEARDOWN_ADMIN_KEY_FILE": str(tmp_path / "missing-admin-secret"),
        "TEARDOWN_MODEL_KEY_FILE": str(tmp_path / "missing-model-secret"),
    })
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/setup_compose.py"), "down"],
        env=environment, capture_output=True, text=True, check=True,
    )
    services = json.loads(result.stdout)["services"]
    api_environment = services["api"]["environment"]
    assert api_environment["TEARDOWN_API_KEY"] == cached_key
    assert api_environment["TEARDOWN_ADMIN_KEY"]
    assert api_environment["TEARDOWN_MODEL_KEY"]
    assert api_environment[database_name]
    if not external_database:
        assert services["postgres"]["environment"]["POSTGRES_PASSWORD"]


@pytest.mark.parametrize("target", [
    "/", "/tmp", "/app", "/app/docs", "/opt/data", "/usr/share/docs",
    "/etc/docs", "/proc/docs", "/sys/docs", "/dev/docs", "/run/docs",
    "/var/lib", "/var/lib/kip/cas/docs", "/data/cas/docs",
])
def test_mirrored_source_targets_cannot_shadow_runtime(target: str) -> None:
    with pytest.raises(ValueError, match="runtime"):
        validate_container_source_target(target)


def test_dedicated_tmp_source_target_is_supported() -> None:
    validate_container_source_target("/tmp/chosen-documents")


def test_host_and_container_settings_share_snapshot_and_exact_evidence(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path).model_copy(update={
        "model_provider": "disabled", "model_secret_ref": None, "relation_mining_mode": "disabled",
    })
    source = tmp_path / "한글 shared documents"
    source.mkdir()
    source_answer = answers.filesystem_sources[0].model_copy(update={"root": str(source.resolve())})
    answers = answers.model_copy(update={"filesystem_sources": [source_answer]})
    path = source / "policy.txt"
    path.write_text("sharednamespaceproof 승인 근거", encoding="utf-8")
    os.utime(path, (1, 1))
    plan = build_setup_plan(answers, project_root=project)
    apply_setup_plan(plan, project_root=project)
    monkeypatch.setenv("KIP_PROJECT_ROOT", str(ROOT))
    monkeypatch.setenv("KIP_ENV", "test")
    monkeypatch.setenv("KIP_WORKSPACE", plan.workspace)
    monkeypatch.setenv("KIP_DATABASE_URL", "memory://")
    monkeypatch.delenv("KIP_DATABASE_URL_FILE", raising=False)
    # Simulate the shared CAS bind without creating container-owned host paths.
    monkeypatch.setenv("KIP_CAS_PATH", plan.cas_path)
    host_settings = Settings.load(project / "config/kip.host.generated.toml")
    container_settings = Settings.load(project / "config/kip.generated.toml")
    host_source = ConfiguredSourceCatalog(host_settings).filesystem(source_answer.name)
    container_source = ConfiguredSourceCatalog(container_settings).filesystem(source_answer.name)
    assert host_source.root == container_source.root == source.resolve()
    assert host_source.acl_snapshot.id == container_source.acl_snapshot.id
    for settings in (host_settings, container_settings):
        settings.raw["parsers"]["isolation"]["enabled"] = False
    repository = MemoryRepository()
    host = build_container(host_settings, repository=repository, load_models=False)
    context = host.application.operations.request_context()
    host.application.ingestion.sync_filesystem(context, source_answer.name)
    hit = host.application.retrieval.search(context, SearchRequest(query="sharednamespaceproof"))[0]
    container = build_container(container_settings, repository=repository, load_models=False)
    assert container.application.retrieval.search(context, SearchRequest(query="sharednamespaceproof"))[0].unit_id == hit.unit_id
    assert container.application.evidence.read_unit(context, hit.unit_id).unit.id == hit.unit_id
    second_path = source / "container-policy.txt"
    second_path.write_text("containernamespaceproof 반환 근거", encoding="utf-8")
    os.utime(second_path, (1, 1))
    container.application.ingestion.sync_filesystem(context, source_answer.name)
    reverse_hit = container.application.retrieval.search(
        context, SearchRequest(query="containernamespaceproof"),
    )[0]
    host = build_container(host_settings, repository=repository, load_models=False)
    assert host.application.evidence.read_unit(context, reverse_hit.unit_id).unit.id == reverse_hit.unit_id


def test_mirrored_source_targets_must_be_unique(tmp_path: Path) -> None:
    project = _project(tmp_path)
    answers = complete_setup_answers(tmp_path)
    first = answers.filesystem_sources[0]
    answers = answers.model_copy(update={
        "filesystem_sources": [first, first.model_copy(update={"name": "duplicate-docs"})],
    })
    with pytest.raises(ValidationError, match="unique"):
        build_setup_plan(answers, project_root=project)
