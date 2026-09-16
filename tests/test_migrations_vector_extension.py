"""Migration 0029 brings pgvector's catalog version up to the running library.

The static checks always run. The database checks use the test-designated
`KIP_TEST_POSTGRES_URL` through the `postgres_database_url` fixture and are
skipped without it.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.repository.postgres import PostgresRepository
from kip.domain.models import MigrationReport

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
MIGRATION = MIGRATIONS / "0029_vector_extension_update.sql"


def _statements_without_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def test_vector_update_migration_is_applied_after_the_hnsw_migrations() -> None:
    assert MIGRATION.is_file()
    ordered = [path.name for path in sorted(MIGRATIONS.glob("*.sql")) if not path.name.startswith("9")]
    position = ordered.index(MIGRATION.name)
    for predecessor in (
        "0006_pgvector_1024_projection.sql",
        "0018_embeddings_1024_hnsw.sql",
        "0022_embeddings_1536_projection.sql",
    ):
        assert ordered.index(predecessor) < position


def test_vector_update_migration_is_guarded_and_does_not_reindex() -> None:
    body = _statements_without_comments(MIGRATION.read_text(encoding="utf-8"))

    assert "ALTER EXTENSION vector UPDATE;" in body
    assert "default_version IS DISTINCT FROM installed.extversion" in body
    # An explicit target version would pin the migration to one image.
    assert not re.search(r"UPDATE\s+TO", body, re.IGNORECASE)
    assert "REINDEX" not in body.upper()
    # Ownership, a catalog newer than the image, and a lock timeout must not
    # fail the migration.
    assert "WHEN insufficient_privilege OR invalid_parameter_value" in body
    assert "OR lock_not_available OR query_canceled THEN" in body
    assert "lock_timeout" in body
    # The runner sends parameterless SQL, but a `%` would still break a
    # future parameterised execution.
    assert "%" not in body


def _vector_versions(connection) -> tuple[str, str]:
    row = connection.execute(
        "SELECT installed.extversion, available.default_version"
        " FROM pg_extension AS installed"
        " JOIN pg_available_extensions AS available ON available.name = installed.extname"
        " WHERE installed.extname = 'vector'"
    ).fetchone()
    assert row is not None, "the vector extension is not installed"
    return row[0], row[1]


def test_migrate_leaves_vector_at_the_server_default_and_reruns_as_a_no_op(
    postgres_database_url: str,
) -> None:
    import psycopg

    repository = PostgresRepository(postgres_database_url)
    try:
        repository.operations.migrate(MIGRATIONS)
        assert MIGRATION.name not in repository.operations.migrate(MIGRATIONS).applied
    finally:
        repository.database.close()

    sql = MIGRATION.read_text(encoding="utf-8")
    role = "kip_vector_nonowner_" + uuid.uuid4().hex[:12]
    with psycopg.connect(postgres_database_url, autocommit=True) as connection:
        installed, default = _vector_versions(connection)
        assert installed == default
        recorded = connection.execute(
            "SELECT 1 FROM kip.schema_migrations WHERE version = %s", (MIGRATION.stem,)
        ).fetchone()
        assert recorded is not None

        connection.execute(sql)
        assert _vector_versions(connection) == (installed, default)

        # Already current: a role that does not own the extension runs it
        # without reaching ALTER EXTENSION's ownership check.
        connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER')
        try:
            connection.execute(f'SET ROLE "{role}"')
            connection.execute(sql)
            connection.execute("RESET ROLE")
        finally:
            connection.execute("RESET ROLE")
            connection.execute(f'DROP ROLE "{role}"')


def test_migration_report_keeps_warnings_out_of_the_envelope_data() -> None:
    report = MigrationReport(
        applied=["0029_vector_extension_update.sql"],
        extension_updates={"vector": {"from": "0.8.2", "to": "0.8.6"}},
        warnings=["operator guidance"],
    )

    assert report.model_dump(mode="json") == {
        "applied": ["0029_vector_extension_update.sql"],
        "extension_updates": {"vector": {"from": "0.8.2", "to": "0.8.6"}},
    }
    assert report.warnings == ["operator guidance"]


def _cli_migrate(monkeypatch, test_container) -> dict:
    import json

    from typer.testing import CliRunner

    from kip.cli import app

    monkeypatch.setattr("kip.cli.build_container", lambda settings, load_models=True: test_container)
    result = CliRunner().invoke(app, ["migrate"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_cli_migrate_envelope_keeps_applied_and_adds_extension_updates(
    monkeypatch, test_container
) -> None:
    envelope = _cli_migrate(monkeypatch, test_container)

    assert envelope["ok"] is True
    assert set(envelope["data"]) == {"applied", "extension_updates"}
    # The fixture's project root has no migrations directory.
    assert envelope["data"]["applied"] == []
    assert envelope["data"]["extension_updates"] == {}
    assert envelope["meta"]["warnings"] == []


def test_cli_migrate_puts_extension_warnings_in_meta(monkeypatch, test_container) -> None:
    report = MigrationReport(applied=[], warnings=["extension vector is at 0.8.2 ..."])
    monkeypatch.setattr(test_container.application.operations, "migrate", lambda: report)

    envelope = _cli_migrate(monkeypatch, test_container)

    assert envelope["ok"] is True
    assert envelope["data"] == {"applied": [], "extension_updates": {}}
    assert envelope["meta"]["warnings"] == ["extension vector is at 0.8.2 ..."]


def test_memory_migrate_reports_no_extension_updates() -> None:
    report = MemoryRepository().operations.migrate(MIGRATIONS)

    assert MIGRATION.name in report.applied
    assert report.extension_updates == {}
    assert report.warnings == []


# A pgvector image installs only its own version, so no server can CREATE the
# older extension. Rewriting pg_extension.extversion in a scratch database
# reproduces a volume that kept its catalog across an image change; the
# 0.8.x update scripts are empty, so ALTER EXTENSION runs the real update path.
@pytest.fixture()
def scratch_database(postgres_database_url: str) -> Iterator[str]:
    import psycopg

    database = "kip_vector_runner_" + uuid.uuid4().hex[:12]
    with psycopg.connect(postgres_database_url, autocommit=True) as connection:
        try:
            connection.execute(f'CREATE DATABASE "{database}"')
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("the test database role cannot create a scratch database")
    url = urlunsplit(urlsplit(postgres_database_url)._replace(path=f"/{database}"))
    try:
        yield url
    finally:
        with psycopg.connect(postgres_database_url, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


def _migrate(url: str) -> MigrationReport:
    repository = PostgresRepository(url)
    try:
        return repository.operations.migrate(MIGRATIONS)
    finally:
        repository.database.close()


def _set_catalog_version(url: str, version: str) -> None:
    import psycopg

    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("UPDATE pg_extension SET extversion = %s WHERE extname = 'vector'", (version,))


def test_every_migrate_updates_a_stale_vector_catalog_with_nothing_pending(
    scratch_database: str,
) -> None:
    import psycopg

    first = _migrate(scratch_database)
    assert MIGRATION.name in first.applied
    assert first.extension_updates == {}

    # 0029 is recorded; the image "changes" underneath the database.
    _set_catalog_version(scratch_database, "0.8.5")
    with psycopg.connect(scratch_database, autocommit=True) as connection:
        _, default = _vector_versions(connection)

    updated = _migrate(scratch_database)
    assert updated.applied == []
    assert updated.extension_updates == {"vector": {"from": "0.8.5", "to": default}}
    assert updated.warnings == []
    with psycopg.connect(scratch_database, autocommit=True) as connection:
        assert _vector_versions(connection) == (default, default)

    again = _migrate(scratch_database)
    assert (again.applied, again.extension_updates, again.warnings) == ([], {}, [])


def test_a_role_that_does_not_own_vector_gets_a_warning_not_a_failure(
    scratch_database: str,
) -> None:
    import psycopg

    _migrate(scratch_database)
    role = "kip_vector_nonowner_" + uuid.uuid4().hex[:12]
    with psycopg.connect(scratch_database, autocommit=True) as connection:
        connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER')
        connection.execute(f'GRANT USAGE ON SCHEMA kip TO "{role}"')
        connection.execute(f'GRANT SELECT ON kip.schema_migrations TO "{role}"')
    # The session connects as the test owner and acts as the role, so no
    # password is needed.
    as_role = urlunsplit(urlsplit(scratch_database)._replace(query=f"options=-c%20role%3D{role}"))
    try:
        _set_catalog_version(scratch_database, "0.8.5")
        report = _migrate(as_role)
        assert report.applied == []
        assert report.extension_updates == {}
        assert len(report.warnings) == 1
        assert "does not own the extension" in report.warnings[0]
        assert "ALTER EXTENSION vector UPDATE;" in report.warnings[0]
        with psycopg.connect(scratch_database, autocommit=True) as connection:
            assert _vector_versions(connection)[0] == "0.8.5"
    finally:
        with psycopg.connect(scratch_database, autocommit=True) as connection:
            connection.execute(f'DROP OWNED BY "{role}"')
            connection.execute(f'DROP ROLE "{role}"')


def test_a_catalog_newer_than_the_image_gets_a_warning_not_a_failure(
    scratch_database: str,
) -> None:
    _migrate(scratch_database)
    _set_catalog_version(scratch_database, "9.9.9")

    report = _migrate(scratch_database)

    assert report.applied == []
    assert report.extension_updates == {}
    assert len(report.warnings) == 1
    assert "PostgreSQL image is older than this database" in report.warnings[0]


def _forget_0029(url: str) -> None:
    import psycopg

    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("DELETE FROM kip.schema_migrations WHERE version = %s", (MIGRATION.stem,))


def _nonowner_url(url: str, role: str) -> str:
    return urlunsplit(urlsplit(url)._replace(query=f"options=-c%20role%3D{role}"))


def test_pending_0029_as_a_role_that_does_not_own_vector_exits_with_one_warning(
    scratch_database: str,
) -> None:
    import psycopg

    _migrate(scratch_database)
    _forget_0029(scratch_database)
    _set_catalog_version(scratch_database, "0.8.5")
    role = "kip_vector_nonowner_" + uuid.uuid4().hex[:12]
    with psycopg.connect(scratch_database, autocommit=True) as connection:
        connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER')
        connection.execute(f'GRANT USAGE ON SCHEMA kip TO "{role}"')
        connection.execute(f'GRANT SELECT, INSERT ON kip.schema_migrations TO "{role}"')
    try:
        report = _migrate(_nonowner_url(scratch_database, role))

        assert report.applied == [MIGRATION.name]
        assert report.extension_updates == {}
        assert len(report.warnings) == 1
        assert "does not own the extension" in report.warnings[0]
        with psycopg.connect(scratch_database, autocommit=True) as connection:
            assert _vector_versions(connection)[0] == "0.8.5"
    finally:
        with psycopg.connect(scratch_database, autocommit=True) as connection:
            connection.execute(f'DROP OWNED BY "{role}"')
            connection.execute(f'DROP ROLE "{role}"')


def test_pending_0029_with_a_catalog_newer_than_the_image_exits_with_one_warning(
    scratch_database: str,
) -> None:
    _migrate(scratch_database)
    _forget_0029(scratch_database)
    _set_catalog_version(scratch_database, "9.9.9")

    report = _migrate(scratch_database)

    assert report.applied == [MIGRATION.name]
    assert report.extension_updates == {}
    assert len(report.warnings) == 1
    assert "PostgreSQL image is older than this database" in report.warnings[0]


def test_an_update_blocked_by_another_session_is_a_warning(
    scratch_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psycopg

    from kip.adapters.repository.postgres import database

    _migrate(scratch_database)
    _set_catalog_version(scratch_database, "0.8.5")
    monkeypatch.setattr(database, "_EXTENSION_UPDATE_LOCK_TIMEOUT_MS", 200)
    with psycopg.connect(scratch_database) as holder:
        holder.execute("ALTER EXTENSION vector UPDATE")  # left uncommitted
        report = _migrate(scratch_database)
        holder.rollback()

    assert report.applied == []
    assert report.extension_updates == {}
    assert len(report.warnings) == 1
    assert "did not complete" in report.warnings[0]


def test_an_update_another_migrate_finished_first_reports_nothing(
    scratch_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    import psycopg

    from kip.adapters.repository.postgres import database

    _migrate(scratch_database)
    _set_catalog_version(scratch_database, "0.8.5")
    monkeypatch.setattr(database, "_EXTENSION_UPDATE_LOCK_TIMEOUT_MS", 30000)
    with psycopg.connect(scratch_database) as holder:
        holder.execute("ALTER EXTENSION vector UPDATE")
        # The holder commits while this migrate waits on the same row; the
        # waiting ALTER then fails with "tuple concurrently updated".
        committer = threading.Timer(1.0, holder.commit)
        committer.start()
        report = _migrate(scratch_database)
        committer.join()

    assert report.applied == []
    assert report.extension_updates == {}
    assert report.warnings == []
    with psycopg.connect(scratch_database, autocommit=True) as connection:
        installed, default = _vector_versions(connection)
        assert installed == default


def test_extension_versions_reports_each_side_on_its_own(scratch_database: str) -> None:
    import psycopg

    _migrate(scratch_database)
    repository = PostgresRepository(scratch_database)
    try:
        with psycopg.connect(scratch_database, autocommit=True) as connection:
            _, default = _vector_versions(connection)
            shipped_only = connection.execute(
                "SELECT name, default_version FROM pg_available_extensions"
                " WHERE installed_version IS NULL ORDER BY name LIMIT 1"
            ).fetchone()
        assert repository.database.extension_versions("vector") == (default, default)
        assert repository.database.extension_versions("kip_no_such_extension") == (None, None)
        if shipped_only is not None:
            assert repository.database.extension_versions(shipped_only[0]) == (None, shipped_only[1])

        # Installed in the database, but the server has no files for it.
        with psycopg.connect(scratch_database, autocommit=True) as connection:
            connection.execute("UPDATE pg_extension SET extname = 'kip_vanished' WHERE extname = 'vector'")
        assert repository.database.extension_versions("kip_vanished") == (default, None)
    finally:
        repository.database.close()
