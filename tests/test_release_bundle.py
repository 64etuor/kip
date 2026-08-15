from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
PINNED_IMAGE = "registry.example/kip@sha256:" + "1" * 64


def _test_wheel(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"kip_knowledge_fabric-{VERSION}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: kip-knowledge-fabric\nVersion: {VERSION}\n",
        )
        archive.writestr(
            f"kip_knowledge_fabric-{VERSION}.dist-info/WHEEL",
            "Wheel-Version: 1.0\nTag: py3-none-any\n",
        )


def _build_bundle(tmp_path: Path) -> Path:
    wheel = tmp_path / f"kip_knowledge_fabric-{VERSION}-py3-none-any.whl"
    _test_wheel(wheel)
    output = tmp_path / "bundle"
    environment = {
        **os.environ,
        "KIP_RELEASE_WHEEL": str(wheel),
        "KIP_API_IMAGE": PINNED_IMAGE,
        "KIP_WORKER_IMAGE": PINNED_IMAGE,
        "KIP_MIGRATE_IMAGE": PINNED_IMAGE,
        "KIP_RELEASE_ALLOW_DIRTY": "1",
        "SOURCE_DATE_EPOCH": "1786262400",
    }
    result = subprocess.run(
        [str(ROOT / "scripts/release-bundle.sh"), str(output)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return output


def test_release_bundle_contains_verified_starter_artifacts(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)

    required = [
        f"artifacts/wheels/kip_knowledge_fabric-{VERSION}-py3-none-any.whl",
        "artifacts/images.lock.json",
        "artifacts/sbom.spdx.json",
        "artifacts/provenance.intoto.json",
        "starter/compose.production.yaml",
        "starter/.env.example",
        "starter/.mcp.json",
        "starter/.claude/skills/kip-setup/SKILL.md",
        "starter/migrations/0012_query_traces.sql",
        "starter/migrations/0021_discovery_candidate_spec.sql",
        "starter/ontology/core/predicates.yaml",
        "starter/contracts/setup-plan.schema.json",
        "starter/contracts/evaluation-review-bundle.schema.json",
        "starter/contracts/golden-draft.schema.json",
        "starter/contracts/golden-draft-review.schema.json",
        "starter/skills/kip-setup/SKILL.md",
        "starter/docs/STARTER_KIT_GUIDE.md",
        "RELEASE-MANIFEST.json",
        "SHA256SUMS",
    ]
    for relative in required:
        assert (bundle / relative).is_file(), relative

    image_lock = json.loads(
        (bundle / "artifacts/images.lock.json").read_text(encoding="utf-8")
    )
    assert set(image_lock["images"]) == {"api", "worker", "migrate"}
    assert all("@sha256:" in value for value in image_lock["images"].values())

    verified = subprocess.run(
        [str(ROOT / "scripts/verify-release.sh"), str(bundle)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verified.returncode == 0, verified.stderr
    archive = tmp_path / "bundle.tar.gz"
    assert archive.is_file()
    archived = subprocess.run(
        [str(ROOT / "scripts/verify-release.sh"), str(archive)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert archived.returncode == 0, archived.stderr


def test_release_bundle_excludes_private_onedrive_golden_corpus(tmp_path: Path) -> None:
    bundle = _build_bundle(tmp_path)

    excluded = [
        "starter/evaluation/golden/private-onedrive-nl.yaml",
        "starter/evaluation/golden/private-onedrive-nl.floor.json",
    ]
    for relative in excluded:
        assert not (bundle / relative).exists(), relative

    # private-starter.yaml is a deliberately redacted synthetic sample that
    # docs/AI_OPERATOR_RUNBOOK.md and evaluation/README.md instruct operators
    # to run as the starter-kit acceptance template, so it must still ship.
    assert (bundle / "starter/evaluation/golden/private-starter.yaml").is_file()

    verified = subprocess.run(
        [str(ROOT / "scripts/verify-release.sh"), str(bundle)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verified.returncode == 0, verified.stderr


def test_release_verifier_rejects_bundle_containing_private_golden_corpus(
    tmp_path: Path,
) -> None:
    bundle = _build_bundle(tmp_path)
    golden = bundle / "starter/evaluation/golden"
    golden.mkdir(parents=True, exist_ok=True)
    (golden / "private-onedrive-nl.yaml").write_text(
        "schema_version: kip.golden-dataset.v1\nname: private-onedrive-nl\ncases: []\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(ROOT / "scripts/verify-release.sh"), str(bundle)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "private golden corpus" in result.stderr.lower()


def test_release_verifier_rejects_secrets_private_paths_and_state(
    tmp_path: Path,
) -> None:
    bundle = _build_bundle(tmp_path)
    forbidden = bundle / "starter/config/kip.toml"
    database_url = "postgresql://owner:" + "secret@db/kip"
    source_path = "/" + "Users/private/company"
    forbidden.write_text(
        f"database_url = '{database_url}'\nsource = '{source_path}'\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(ROOT / "scripts/verify-release.sh"), str(bundle)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "forbidden" in result.stderr.lower()


def test_production_compose_enforces_isolation_and_digest_images() -> None:
    payload = yaml.safe_load((ROOT / "compose.production.yaml").read_text(encoding="utf-8"))
    services = payload["services"]

    assert "@sha256:" in services["postgres"]["image"]
    for name in ("migrate", "api", "worker"):
        service = services[name]
        assert service["image"].startswith("${KIP_")
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["environment"]["KIP_DATABASE_URL_FILE"].startswith(
            "/run/secrets/"
        )
        assert "kip_owner" not in str(service)
    assert services["api"]["volumes"][0]["read_only"] is True
    assert services["api"]["healthcheck"]["test"][0] == "CMD"
    assert services["worker"]["volumes"][1]["read_only"] is True
    assert payload["networks"]["database"]["internal"] is True

    for name in ("api", "worker"):
        ontology_mounts = [
            volume
            for volume in services[name]["volumes"]
            if volume.get("target") == "/app/ontology"
        ]
        assert ontology_mounts, f"{name} service is missing an /app/ontology bind mount"


def test_runtime_image_is_locked_and_non_root() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "python:3.12-slim@sha256:" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "pip install --upgrade pip" not in dockerfile
