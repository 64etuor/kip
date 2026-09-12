"""Row level security must be forced wherever it is enabled.

ENABLE ROW LEVEL SECURITY is inert for the table owner, and the reference
compose profile connects as the owner of every table. A table that only
enables RLS therefore fails open on that path, which is exactly the failure
mode migration 0016 fixed for six tables and 0028 closed for the rest.

The check runs against a throwaway database, like the other role tests in this
directory: KIP_DATABASE_URL is a live deployment on a developer machine, and a
test has no business running migrations against it.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from kip.adapters.repository.postgres import PostgresRepository

URL = os.environ.get("KIP_TEST_POSTGRES_URL") or os.environ.get("KIP_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="PostgreSQL integration URL not configured")

ROOT = Path(__file__).resolve().parents[2]
KIP_SCHEMAS = (
    "kip",
    "source",
    "content",
    "knowledge",
    "search",
    "jobs",
    "audit",
    "interaction",
)


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


@pytest.fixture
def migrated_database() -> Iterator[str]:
    psycopg = pytest.importorskip("psycopg")

    database = f"kip_rls_force_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(str(URL), autocommit=True) as admin:
        try:
            admin.execute(f'CREATE DATABASE "{database}"')
        except psycopg.errors.InsufficientPrivilege:
            pytest.skip("the configured role cannot create a throwaway database")

    target = _with_database(str(URL), database)
    try:
        PostgresRepository(target).operations.migrate(ROOT / "migrations")
        yield target
    finally:
        with psycopg.connect(str(URL), autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                (database,),
            )
            admin.execute(f'DROP DATABASE IF EXISTS "{database}"')


def test_every_rls_enabled_table_also_forces_rls(migrated_database: str) -> None:
    psycopg = pytest.importorskip("psycopg")

    with psycopg.connect(migrated_database) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT namespace_entry.nspname || '.' || class_entry.relname,
                   class_entry.relforcerowsecurity
            FROM pg_class AS class_entry
            JOIN pg_namespace AS namespace_entry
                ON namespace_entry.oid = class_entry.relnamespace
            WHERE class_entry.relkind = 'r'
              AND class_entry.relrowsecurity
              AND namespace_entry.nspname = ANY(%s)
            ORDER BY 1
            """,
            (list(KIP_SCHEMAS),),
        )
        rows = cursor.fetchall()

    assert rows, "no RLS-enabled table found; the schema was not migrated"
    unforced = [name for name, forced in rows if not forced]

    assert not unforced, (
        "tables enable row level security without forcing it, so the table "
        f"owner bypasses their policies: {unforced}"
    )
