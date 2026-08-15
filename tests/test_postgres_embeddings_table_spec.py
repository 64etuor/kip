"""Unit tests for the PostgreSQL adapter's provisioned-embeddings-table
lookup (`_EMBEDDING_TABLES` / `_embeddings_table` / `_embeddings_union_sql`).

These are pure functions over a closed, in-module dict -- no live connection
needed. They decide which fixed-width `pgvector` table backs a given
embedding dimensionality, replacing the previous `!= 1024` hard guards.
"""

from __future__ import annotations

import pytest

from kip.adapters.repository.postgres.database import (
    _EMBEDDING_TABLES,
    _embeddings_table,
    _embeddings_union_sql,
)
from kip.errors import ValidationError


def test_embeddings_table_resolves_every_provisioned_dimension() -> None:
    assert _embeddings_table(1024) == "search.embeddings_1024"
    assert _embeddings_table(1536) == "search.embeddings_1536"


def test_embeddings_table_matches_the_registry_for_every_provisioned_entry() -> None:
    for dimensions, table in _EMBEDDING_TABLES.items():
        assert _embeddings_table(dimensions) == table


def test_embeddings_table_rejects_an_unprovisioned_dimension_with_the_provisioned_list() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _embeddings_table(768)

    message = str(excinfo.value)
    assert "768" in message
    assert "1024" in message
    assert "1536" in message


def test_embeddings_union_sql_selects_the_given_columns_from_every_table() -> None:
    sql = _embeddings_union_sql("workspace_id, unit_id")

    assert sql.count("UNION ALL") == len(_EMBEDDING_TABLES) - 1
    for table in _EMBEDDING_TABLES.values():
        assert f"SELECT workspace_id, unit_id FROM {table}" in sql
