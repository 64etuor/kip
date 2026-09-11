from __future__ import annotations

import time

import pytest

from kip.errors import DependencyUnavailableError


def test_unreachable_database_fails_fast_with_actionable_typed_error():
    pytest.importorskip("psycopg_pool")
    from kip.adapters.repository.postgres.database import PostgresDatabase

    database = PostgresDatabase("postgresql://kip_owner:test-password@127.0.0.1:1/kip")
    started = time.monotonic()
    try:
        with pytest.raises(DependencyUnavailableError) as failure:
            database.ping()
    finally:
        database.close()

    assert time.monotonic() - started < 20
    message = str(failure.value)
    assert "127.0.0.1:1/kip" in message
    assert "app-up.sh --database-only" in message
    assert "test-password" not in message
