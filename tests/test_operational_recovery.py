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
    project = tmp_path / "package"
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


def _database_manifest_file(path: Path, extensions: dict[str, str]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "kip.database-backup-manifest.v1",
                "database": "kip",
                "server_version_num": 180003,
                "row_security": "off",
                "migrations": ["0001_extensions_and_schemas"],
                "extensions": extensions,
                "rls_policy_count": 3,
                "counts": {"workspaces": 1},
            }
        ),
        encoding="utf-8",
    )
    return path


def _compare_extensions(
    tmp_path: Path, backup: dict[str, str], restored: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return _run_artifacts(
        "compare-database",
        "--expected",
        str(_database_manifest_file(tmp_path / "expected.json", backup)),
        "--actual",
        str(_database_manifest_file(tmp_path / "actual.json", restored)),
    )


def test_restore_comparison_accepts_a_newer_vector_patch_release(tmp_path: Path) -> None:
    # A backup of a volume still at 0.8.2 restored onto the 0.8.6 image:
    # pg_restore recreates the extension at the server's default version.
    result = _compare_extensions(
        tmp_path,
        {"pg_trgm": "1.6", "plpgsql": "1.0", "vector": "0.8.2"},
        {"pg_trgm": "1.6", "plpgsql": "1.0", "vector": "0.8.6"},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "verified"
    assert payload["extension_updates"] == {"vector": {"backup": "0.8.2", "restored": "0.8.6"}}


def test_restore_comparison_orders_two_digit_patch_releases_numerically(tmp_path: Path) -> None:
    result = _compare_extensions(tmp_path, {"vector": "0.8.9"}, {"vector": "0.8.10"})

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["extension_updates"] == {
        "vector": {"backup": "0.8.9", "restored": "0.8.10"}
    }


def test_restore_comparison_reports_no_update_for_identical_extensions(tmp_path: Path) -> None:
    extensions = {"pg_trgm": "1.6", "vector": "0.8.6"}
    result = _compare_extensions(tmp_path, extensions, dict(extensions))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["extension_updates"] == {}


def test_restore_comparison_rejects_every_other_extension_difference(tmp_path: Path) -> None:
    rejected_pairs = [
        # a downgrade, including one a string comparison would call newer
        ({"vector": "0.8.6"}, {"vector": "0.8.2"}),
        ({"vector": "0.8.10"}, {"vector": "0.8.9"}),
        # a newer minor line changes SQL objects
        ({"vector": "0.8.6"}, {"vector": "0.9.0"}),
        ({"vector": "0.7.4"}, {"vector": "0.8.6"}),
        # a newer major
        ({"vector": "0.8.2"}, {"vector": "1.8.2"}),
        # not a release version
        ({"vector": "0.8.2"}, {"vector": "0.8.6-dev"}),
        # only vector may move
        ({"pg_trgm": "1.5", "vector": "0.8.2"}, {"pg_trgm": "1.6", "vector": "0.8.2"}),
        # the extension set must be the same
        ({"vector": "0.8.2"}, {"vector": "0.8.6", "pg_trgm": "1.6"}),
        ({"pg_trgm": "1.6", "vector": "0.8.2"}, {"vector": "0.8.6"}),
    ]
    for index, (backup, restored) in enumerate(rejected_pairs):
        case = tmp_path / str(index)
        case.mkdir()
        result = _compare_extensions(case, backup, restored)
        assert result.returncode != 0, (backup, restored)
        assert "extensions do not match the backup" in result.stderr, (backup, restored)


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
