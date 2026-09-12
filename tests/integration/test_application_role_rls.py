"""The API/worker database role must not be able to read another workspace.

Compose sets POSTGRES_USER=kip_owner and PostgreSQL creates that bootstrap role
as a SUPERUSER with BYPASSRLS, so every policy in this schema is inert on that
connection: migration 0028 can force row level security on all 34 tables and
the owner still reads every workspace. The fix is
`deploy/sql/roles.sql.template`, applied after migrations, plus login roles
bound to its non-superuser groups.

This test builds a throwaway database, applies the template there, and compares
what the same query returns for the owner and for a login bound to kip_api. The
roles the template creates are cluster-wide and NOLOGIN; creating them is
idempotent. Only the throwaway database and the throwaway login are removed.
"""

from __future__ import annotations

import os
import uuid
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


def _seed(cursor, workspace: str, public_id: str) -> None:
    cursor.execute(
        "INSERT INTO kip.workspaces(slug, name) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING",
        (workspace, workspace),
    )
    cursor.execute(
        """
        INSERT INTO audit.events(public_id, workspace_id, action, object_type)
        VALUES (%s, %s, 'probe', 'test')
        """,
        (public_id, workspace),
    )


def test_kip_api_login_cannot_read_another_workspace_where_the_owner_can() -> None:
    psycopg = pytest.importorskip("psycopg")

    suffix = uuid.uuid4().hex[:12]
    database = f"kip_roles_{suffix}"
    login = f"kip_roles_login_{suffix}"
    password = uuid.uuid4().hex
    first = f"ws_first_{suffix}"
    second = f"ws_second_{suffix}"

    with psycopg.connect(str(URL), autocommit=True) as admin:
        bypasses = admin.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()[0]
        if not bypasses:
            pytest.skip("the configured role already cannot bypass RLS; the owner control is moot")
        try:
            admin.execute(f'CREATE DATABASE "{database}"')
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("the configured role cannot create a throwaway database")

    target = _with_database(str(URL), database)
    try:
        PostgresRepository(target).operations.migrate(ROOT / "migrations")

        with psycopg.connect(target, autocommit=True) as owner, owner.cursor() as cursor:
            _seed(cursor, first, f"evt_first_{suffix}")
            _seed(cursor, second, f"evt_second_{suffix}")
            cursor.execute(ROLES_TEMPLATE.read_text(encoding="utf-8"))
            cursor.execute(
                f"CREATE ROLE \"{login}\" LOGIN INHERIT PASSWORD '{password}'"
            )
            cursor.execute(f'GRANT kip_api TO "{login}"')

            # Every table the schema protects must be readable by kip_api, or
            # the template has gone stale against a later migration and the
            # deployment fails at runtime instead of failing here.
            cursor.execute(
                """
                SELECT namespace_entry.nspname || '.' || class_entry.relname
                FROM pg_class AS class_entry
                JOIN pg_namespace AS namespace_entry
                    ON namespace_entry.oid = class_entry.relnamespace
                WHERE class_entry.relkind = 'r'
                  AND namespace_entry.nspname = ANY(%s)
                  AND NOT has_table_privilege('kip_api', class_entry.oid, 'SELECT')
                ORDER BY 1
                """,
                (list(KIP_SCHEMAS),),
            )
            ungranted = [row[0] for row in cursor.fetchall()]
            assert not ungranted, f"kip_api has no SELECT on {ungranted}"

            # The owner is the control: the same query, the same workspace
            # setting, and it still sees the other workspace's row.
            cursor.execute("SELECT set_config('kip.workspace_id', %s, false)", (first,))
            cursor.execute(
                "SELECT count(*) FROM audit.events WHERE workspace_id IN (%s, %s)",
                (first, second),
            )
            assert cursor.fetchone()[0] == 2

        with psycopg.connect(_with_login(target, login, password)) as member:
            with member.cursor() as cursor:
                cursor.execute(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )
                assert cursor.fetchone() == (False, False)

                cursor.execute("SELECT set_config('kip.workspace_id', %s, false)", (first,))
                cursor.execute(
                    "SELECT count(*) FROM audit.events WHERE workspace_id IN (%s, %s)",
                    (first, second),
                )
                assert cursor.fetchone()[0] == 1
                cursor.execute(
                    "SELECT count(*) FROM audit.events WHERE workspace_id = %s", (second,)
                )
                assert cursor.fetchone()[0] == 0

            member.rollback()
            with member.cursor() as cursor:
                cursor.execute("SELECT set_config('kip.workspace_id', %s, false)", (first,))
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    cursor.execute(
                        """
                        INSERT INTO audit.events(public_id, workspace_id, action, object_type)
                        VALUES (%s, %s, 'probe', 'test')
                        """,
                        (f"evt_cross_{suffix}", second),
                    )

            member.rollback()
            with member.cursor() as cursor:
                # scripts/backup.sh needs this to fail for the application
                # roles: only the BYPASSRLS kip_backup role may turn policies
                # off, otherwise any application session could dump everything.
                cursor.execute("SET row_security = off")
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    cursor.execute("SELECT count(*) FROM audit.events")
    finally:
        with psycopg.connect(str(URL), autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database,),
            )
            admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
            admin.execute(f'DROP ROLE IF EXISTS "{login}"')
