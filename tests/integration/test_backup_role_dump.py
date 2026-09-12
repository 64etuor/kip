"""The backup role must be able to complete a dump, not just connect.

`scripts/bootstrap_env.py` writes KIP_BACKUP_DATABASE_URL for every install and
upgrade and `scripts/backup.sh` prefers it, so kip_backup is the shipped backup
path. The BYPASSRLS preflight in backup.sh only proves the role can turn row
level security off; pg_dump then reads every table *and every sequence*, and a
role with SELECT on the tables alone aborts with "failed to get data for
sequence ...". pg_dump writes nothing before that abort, so the deployment is
left with a zero byte kip.dump and a backup it only discovers is empty during a
restore.

These tests build a throwaway database, apply `deploy/sql/roles.sql.template`
there, and issue exactly the reads pg_dump issues as a login bound to
kip_backup; the second test runs the real pg_dump when the binary is available.
Only the throwaway database and the throwaway login are removed; the
cluster-wide roles the template creates are left alone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from kip.adapters.repository.postgres import PostgresRepository

URL = os.environ.get("KIP_TEST_POSTGRES_URL") or os.environ.get("KIP_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="PostgreSQL integration URL not configured")

ROOT = Path(__file__).resolve().parents[2]
ROLES_TEMPLATE = ROOT / "deploy/sql/roles.sql.template"
KIP_SCHEMAS = ("kip", "source", "content", "knowledge", "search", "jobs", "audit", "interaction")


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def _with_login(url: str, user: str, password: str) -> str:
    parts = urlsplit(url)
    host = f"[{parts.hostname}]" if parts.hostname and ":" in parts.hostname else parts.hostname
    location = f"{user}:{password}@{host}"
    if parts.port:
        location = f"{location}:{parts.port}"
    return urlunsplit((parts.scheme, location, parts.path, parts.query, parts.fragment))


@pytest.fixture(scope="module")
def backup_database() -> Iterator[tuple[str, str]]:
    """A migrated throwaway database, its owner URL and a kip_backup login URL."""

    psycopg = pytest.importorskip("psycopg")

    suffix = uuid.uuid4().hex[:12]
    database = f"kip_backup_{suffix}"
    # BYPASSRLS is a role attribute and role attributes are not inherited
    # through membership, so a plain member of kip_backup cannot set
    # row_security=off. The template's header documents the two options; this
    # is the second one, because the first (logging in as kip_backup itself)
    # would mean giving a shared cluster role a password from a test.
    login = f"kip_backup_login_{suffix}"
    password = uuid.uuid4().hex

    with psycopg.connect(str(URL), autocommit=True) as admin:
        superuser = admin.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ).fetchone()[0]
        if not superuser:
            pytest.skip("creating a BYPASSRLS login role needs a superuser")
        try:
            admin.execute(f'CREATE DATABASE "{database}"')
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("the configured role cannot create a throwaway database")

    target = _with_database(str(URL), database)
    try:
        PostgresRepository(target).operations.migrate(ROOT / "migrations")
        with psycopg.connect(target, autocommit=True) as owner, owner.cursor() as cursor:
            # Move a sequence off its start value: pg_dump reads last_value from
            # every sequence it emits, and it emits all of them.
            cursor.execute(
                "INSERT INTO kip.workspaces(slug, name) VALUES (%s, %s) "
                "ON CONFLICT (slug) DO NOTHING",
                (f"ws_{suffix}", f"ws_{suffix}"),
            )
            cursor.execute(
                """
                INSERT INTO audit.events(public_id, workspace_id, action, object_type)
                VALUES (%s, %s, 'probe', 'test')
                """,
                (f"evt_{suffix}", f"ws_{suffix}"),
            )
            cursor.execute(ROLES_TEMPLATE.read_text(encoding="utf-8"))
            cursor.execute(f"CREATE ROLE \"{login}\" LOGIN INHERIT BYPASSRLS PASSWORD '{password}'")
            cursor.execute(f'GRANT kip_backup TO "{login}"')
        yield target, _with_login(target, login, password)
    finally:
        with psycopg.connect(str(URL), autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database,),
            )
            admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
            admin.execute(f'DROP ROLE IF EXISTS "{login}"')


def test_kip_backup_can_read_every_relation_pg_dump_reads(
    backup_database: tuple[str, str],
) -> None:
    psycopg = pytest.importorskip("psycopg")
    owner_url, backup_url = backup_database

    with psycopg.connect(owner_url, autocommit=True) as owner, owner.cursor() as cursor:
        cursor.execute(
            """
            SELECT name FROM (
                SELECT namespace_entry.nspname || '.' || class_entry.relname AS name,
                       class_entry.oid
                FROM pg_class AS class_entry
                JOIN pg_namespace AS namespace_entry
                    ON namespace_entry.oid = class_entry.relnamespace
                WHERE class_entry.relkind = 'S'
                  AND namespace_entry.nspname = ANY(%s)
                -- OFFSET 0 fences the subquery: without it the planner may
                -- evaluate has_sequence_privilege on a toast relation, which
                -- errors instead of reporting a missing grant.
                OFFSET 0
            ) AS sequences
            WHERE NOT has_sequence_privilege('kip_backup', sequences.oid, 'SELECT')
            ORDER BY 1
            """,
            (list(KIP_SCHEMAS),),
        )
        ungranted = [row[0] for row in cursor.fetchall()]
        assert not ungranted, (
            "kip_backup has no SELECT on these sequences, so pg_dump aborts with "
            f"'failed to get data for sequence' and writes a zero byte dump: {ungranted}"
        )

        cursor.execute(
            """
            SELECT namespace_entry.nspname || '.' || class_entry.relname,
                   class_entry.relkind
            FROM pg_class AS class_entry
            JOIN pg_namespace AS namespace_entry
                ON namespace_entry.oid = class_entry.relnamespace
            WHERE class_entry.relkind IN ('r', 'p', 'S')
              AND namespace_entry.nspname = ANY(%s)
            ORDER BY 1
            """,
            (list(KIP_SCHEMAS),),
        )
        relations = cursor.fetchall()

    assert relations, "no relations found; the throwaway database was not migrated"
    assert any(kind == "S" for _, kind in relations), "no sequence found to check"

    # scripts/backup.sh exports PGOPTIONS=-c row_security=off, and pg_dump then
    # reads every table and `last_value, is_called` from every sequence. Issue
    # the same reads on the same kind of connection: a privilege gap fails here
    # the way it fails inside pg_dump, without needing the pg_dump binary.
    with (
        psycopg.connect(backup_url, autocommit=True, options="-c row_security=off") as backup,
        backup.cursor() as cursor,
    ):
        unreadable = []
        for name, kind in relations:
            schema, _, relation = name.partition(".")
            qualified = f'"{schema}"."{relation}"'
            statement = (
                f"SELECT last_value, is_called FROM {qualified}"
                if kind == "S"
                else f"SELECT count(*) FROM {qualified}"
            )
            try:
                cursor.execute(statement)
                cursor.fetchall()
            except psycopg.errors.InsufficientPrivilege:
                unreadable.append(name)
        assert not unreadable, (
            f"the backup role cannot read {unreadable}; pg_dump would abort part way "
            "through and leave a zero byte backup"
        )


def test_pg_dump_as_the_backup_role_writes_a_non_empty_dump(
    backup_database: tuple[str, str], tmp_path: Path
) -> None:
    pg_dump = os.environ.get("PG_DUMP") or shutil.which("pg_dump")
    if pg_dump is None:
        pytest.skip("pg_dump is not on PATH")
    _, backup_url = backup_database

    dump = tmp_path / "kip.dump"
    # A fixed executable and a throwaway database; no shell.
    completed = subprocess.run(
        [
            pg_dump,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--file={dump}",
            backup_url,
        ],
        env={**os.environ, "PGOPTIONS": "-c row_security=off"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, f"pg_dump failed: {completed.stderr}"
    assert dump.exists() and dump.stat().st_size > 0, "pg_dump wrote a zero byte backup"
