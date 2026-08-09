from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_artifacts(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ROOT / "scripts/backup_artifacts.py"), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_dotenv_loader_preserves_explicit_environment_without_shell_evaluation(
    tmp_path: Path,
) -> None:
    project = tmp_path / "starter"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/common.sh", scripts / "common.sh")
    shutil.copy2(ROOT / "scripts/load_dotenv.py", scripts / "load_dotenv.py")
    marker = project / "executed"
    (project / ".env").write_text(
        "EXPLICIT=from-file\n"
        "MISSING=loaded\n"
        f"UNTRUSTED=$(touch {marker})\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "bash",
            "-c",
            "source scripts/common.sh; printf '%s|%s|%s' \"$EXPLICIT\" \"$MISSING\" \"$UNTRUSTED\"",
        ],
        cwd=project,
        env={**os.environ, "EXPLICIT": "from-process"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"from-process|loaded|$(touch {marker})"
    assert not marker.exists()


def test_cas_backup_round_trip_is_hash_verified_and_symlink_safe(tmp_path: Path) -> None:
    cas = tmp_path / "cas"
    (cas / "objects/aa").mkdir(parents=True)
    (cas / "objects/aa/first").write_bytes(b"first")
    (cas / "objects/second").write_bytes(b"second")
    backup = tmp_path / "backup"
    backup.mkdir()

    snapshot = _run_artifacts(
        "snapshot-cas",
        "--source",
        str(cas),
        "--archive",
        str(backup / "cas.tar.gz"),
        "--manifest",
        str(backup / "cas-manifest.json"),
    )
    assert snapshot.returncode == 0, snapshot.stderr

    restored = tmp_path / "restored"
    restore = _run_artifacts(
        "restore-cas",
        "--archive",
        str(backup / "cas.tar.gz"),
        "--manifest",
        str(backup / "cas-manifest.json"),
        "--target",
        str(restored),
    )
    assert restore.returncode == 0, restore.stderr
    assert (restored / "objects/aa/first").read_bytes() == b"first"
    assert (restored / "objects/second").read_bytes() == b"second"

    (cas / "linked").symlink_to(cas / "objects/second")
    rejected = _run_artifacts(
        "snapshot-cas",
        "--source",
        str(cas),
        "--archive",
        str(tmp_path / "unsafe.tar.gz"),
        "--manifest",
        str(tmp_path / "unsafe.json"),
    )
    assert rejected.returncode != 0
    assert "symlink" in rejected.stderr.lower()


def test_sealed_backup_detects_payload_tampering(tmp_path: Path) -> None:
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "kip.dump").write_bytes(b"custom-format-fixture")
    (backup / "canonical.jsonl").write_text("{}\n", encoding="utf-8")
    (backup / "canonical-export-receipt.json").write_text("{}\n", encoding="utf-8")
    (backup / "database-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "kip.database-backup-manifest.v1",
                "database": "kip",
                "server_version_num": 180000,
                "row_security": "off",
                "migrations": ["0001"],
                "counts": {"workspaces": 1},
            }
        ),
        encoding="utf-8",
    )
    empty_cas = tmp_path / "cas"
    empty_cas.mkdir()
    assert (
        _run_artifacts(
            "snapshot-cas",
            "--source",
            str(empty_cas),
            "--archive",
            str(backup / "cas.tar.gz"),
            "--manifest",
            str(backup / "cas-manifest.json"),
        ).returncode
        == 0
    )
    config = _run_artifacts(
        "snapshot-config",
        "--root",
        str(ROOT),
        "--archive",
        str(backup / "configuration.tar.gz"),
    )
    assert config.returncode == 0, config.stderr
    sealed = _run_artifacts("seal", str(backup))
    assert sealed.returncode == 0, sealed.stderr
    assert _run_artifacts("verify", str(backup)).returncode == 0

    with (backup / "canonical.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"tampered":true}\n')
    rejected = _run_artifacts("verify", str(backup))
    assert rejected.returncode != 0
    assert "checksum" in rejected.stderr.lower()


def test_recovery_scripts_encode_non_destructive_database_contract() -> None:
    backup = (ROOT / "scripts/backup.sh").read_text(encoding="utf-8")
    restore = (ROOT / "scripts/restore.sh").read_text(encoding="utf-8")
    drill = (ROOT / "scripts/restore-drill.sh").read_text(encoding="utf-8")

    assert "--no-owner" in backup
    assert "--no-privileges" in backup
    assert "row_security=off" in backup
    assert "export canonical" in backup
    assert "--clean" not in restore
    assert "--single-transaction" in restore
    assert "--exit-on-error" in restore
    assert '"ANALYZE;"' in restore
    assert "KIP_RESTORE_CONFIRM" in restore
    assert "KIP_RESTORE_DRILL_CONFIRM" in drill
    assert "KIP_DRILL_GOLDEN_DATASET" in drill
