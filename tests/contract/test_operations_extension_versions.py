"""`OperationsStore.extension_versions` across adapters.

The PostgreSQL case uses the `postgres_database_url` fixture and is skipped
without `KIP_TEST_POSTGRES_URL`.
"""
from __future__ import annotations

from pathlib import Path

from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.repository.postgres import PostgresRepository

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def test_memory_operations_store_has_no_extension_catalog() -> None:
    assert MemoryRepository().operations.extension_versions("vector") is None


def test_postgres_operations_store_reports_the_installed_and_server_vector_versions(
    postgres_database_url: str,
) -> None:
    from kip.cli import _postgres_extensions_doctor_check

    repository = PostgresRepository(postgres_database_url)
    try:
        repository.operations.migrate(MIGRATIONS)
        installed, server = repository.operations.extension_versions("vector") or (None, None)
        missing = repository.operations.extension_versions("kip_no_such_extension")
    finally:
        repository.database.close()

    assert installed and server
    assert missing == (None, None)
    check = _postgres_extensions_doctor_check(repository.operations)
    assert check["details"]["installed_version"] == installed
    assert check["details"]["state"] == ("current" if installed == server else "outdated")
