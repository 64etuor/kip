from __future__ import annotations

import atexit
import hashlib
import json
import math
import threading
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

    from psycopg import Connection
    from psycopg.rows import DictRow

from kip.domain.egress import DataClassification
from kip.domain.identity import AclSnapshot
from kip.domain.knowledge import (
    EntityCandidate,
    KnowledgeEntity,
    RelationDerivation,
    normalize_entity_name,
    stable_entity_id,
)
from kip.domain.models import (
    GRAPH_PATH_RESULT_CAP,
    ApprovedAssertion,
    Artifact,
    ArtifactView,
    AssertionCandidate,
    ContentUnit,
    DocumentPacket,
    EmbeddableUnit,
    EmbeddingRecord,
    EmbeddingSpace,
    EvidenceLocator,
    GraphEdge,
    GraphNeighborsRequest,
    GraphPath,
    GraphPathRequest,
    IngestResult,
    JobRecord,
    LogicalDocument,
    RequestContext,
    SearchHit,
    SearchRequest,
    SourceObject,
    SourceObjectAbsence,
    SourceRevision,
    StatusReport,
    VocabularyItem,
)
from kip.errors import (
    AuthorizationError,
    ConflictError,
    DependencyUnavailableError,
    NotFoundError,
    ValidationError,
)
from kip.ids import new_id, stable_id
from kip.ontology import FALLBACK_EVIDENCE_REQUIRED_PREDICATES

_REVIEW_RISK_ORDER_SQL = (
    "CASE review_risk WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"
)


def _evidence_required(candidate: AssertionCandidate) -> bool:
    """Fail-closed evidence gate applied at approval time.

    The application layer enforces the catalog-derived rule
    (review == "required" or risk == "high"); this store-level check is a
    defense-in-depth floor using the candidate's own recorded review risk
    plus the fallback predicate set pinned to `ontology/core/predicates.yaml`
    by a contract test.
    """
    return (
        candidate.review_risk == "high"
        or candidate.predicate in FALLBACK_EVIDENCE_REQUIRED_PREDICATES
    )


def _json(value: Any) -> str:
    def encode(item: Any) -> Any:
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        return str(item)

    return json.dumps(value, ensure_ascii=False, default=encode)


def _vector_literal(values: list[float]) -> str:
    if any(not math.isfinite(value) for value in values):
        raise ValidationError("embedding contains non-finite values")
    return "[" + ",".join(format(value, ".9g") for value in values) + "]"


# Single source of truth for which fixed-width `pgvector` table backs a given
# embedding dimensionality. Adding a new provisioned dimension means adding a
# migration (mirroring 0006 + 0018) plus one entry here -- no dynamic DDL.
# Table names are drawn only from this closed dict (never from user input),
# so f-string interpolation of the resolved value into SQL is safe and is
# used throughout this module instead of psycopg identifier composition.
_EMBEDDING_TABLES: dict[int, str] = {
    1024: "search.embeddings_1024",
    1536: "search.embeddings_1536",
}


def _embeddings_table(dimensions: int) -> str:
    """Resolve the provisioned `pgvector` table for an embedding dimension.

    Raises `ValidationError` listing the provisioned dimensions when
    `dimensions` has no matching table.
    """
    table = _EMBEDDING_TABLES.get(dimensions)
    if table is None:
        provisioned = ", ".join(str(value) for value in sorted(_EMBEDDING_TABLES))
        raise ValidationError(
            f"unsupported embedding dimensions: {dimensions}; the PostgreSQL "
            f"semantic projection is provisioned for: {provisioned}"
        )
    return table


def _embeddings_union_sql(columns: str) -> str:
    """Build a `UNION ALL` projection of `columns` across every provisioned
    embeddings table, so callers that only know a `space_id` (not its
    dimensionality) can join against whichever table actually holds it.
    """
    return " UNION ALL ".join(
        f"SELECT {columns} FROM {table}" for table in _EMBEDDING_TABLES.values()
    )


def _websearch_or_query(lexemes: str, *, max_terms: int = 64) -> str:
    terms = list(dict.fromkeys(term.replace('"', "") for term in lexemes.split() if term))
    if len(terms) > max_terms:
        # Long natural-language questions expand into hundreds of n-grams;
        # ORing them all pushed queries past the statement timeout. Keep the
        # most selective terms (longer first, stable order within a length)
        # instead of truncating in emission order, which kept mostly 2-grams.
        terms.sort(key=len, reverse=True)
        terms = terms[:max_terms]
    return " OR ".join(f'"{term}"' for term in terms)


class PostgresDatabase:
    """PostgreSQL canonical repository and baseline lexical/graph adapter."""

    name = "postgresql"

    def __init__(
        self,
        database_url: str,
        *,
        statement_timeout_ms: int = 15000,
        pool_max_size: int = 10,
        hnsw_ef_search: int = 200,
        hnsw_max_scan_tuples: int = 100_000,
    ) -> None:
        if hnsw_ef_search <= 0 or hnsw_max_scan_tuples <= 0:
            raise ValidationError("HNSW scan bounds must be positive")
        self.database_url = database_url
        self.statement_timeout_ms = statement_timeout_ms
        self.pool_max_size = pool_max_size
        self.hnsw_ef_search = hnsw_ef_search
        self.hnsw_max_scan_tuples = hnsw_max_scan_tuples
        self._pool: Any = None
        self._pool_lock = threading.Lock()
        try:
            import psycopg  # noqa: F401
            import psycopg_pool  # noqa: F401
        except ImportError as exc:
            raise DependencyUnavailableError(
                "Install the postgres extra: pip install '.[postgres]'"
            ) from exc

    def _connection_pool(self) -> Any:
        if self._pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            with self._pool_lock:
                if self._pool is None:
                    pool = ConnectionPool(
                        self.database_url,
                        min_size=0,
                        max_size=self.pool_max_size,
                        kwargs={"row_factory": dict_row},
                        open=True,
                        name="kip-postgres",
                    )
                    atexit.register(pool.close)
                    self._pool = pool
        return self._pool

    def close(self) -> None:
        with self._pool_lock:
            if self._pool is not None:
                self._pool.close()
                self._pool = None

    @contextmanager
    def _connection(
        self,
        context: RequestContext | None = None,
    ) -> Generator[Connection[DictRow], None, None]:
        with self._connection_pool().connection() as connection:
            with connection.cursor() as cursor:
                if context is not None:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, false),"
                        " set_config('kip.workspace_id', %s, true),"
                        " set_config('kip.principal_id', %s, true),"
                        " set_config('kip.acl_scopes', %s, true),"
                        " set_config('kip.roles', %s, true)",
                        (
                            str(self.statement_timeout_ms),
                            context.workspace,
                            context.principal_id,
                            ",".join(context.acl_scopes),
                            ",".join(sorted(set(context.roles))),
                        ),
                    )
                else:
                    cursor.execute(
                        "SELECT set_config('statement_timeout', %s, false)",
                        (str(self.statement_timeout_ms),),
                    )
            yield connection

    def ping(self) -> None:
        """Readiness probe: a real round-trip using the pooled connection."""
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

    def migrate(self, migrations_dir: Path) -> list[str]:
        import psycopg
        from psycopg.rows import dict_row

        applied: list[str] = []
        with psycopg.connect(
            self.database_url, row_factory=dict_row, autocommit=True
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, false)",
                    (str(self.statement_timeout_ms),),
                )
            for path in sorted(migrations_dir.glob("*.sql")):
                if path.name.startswith("9"):
                    continue
                version = path.stem
                checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                with connection.cursor() as cursor:
                    try:
                        cursor.execute(
                            "SELECT checksum FROM kip.schema_migrations WHERE version = %s",
                            (version,),
                        )
                        row = cursor.fetchone()
                    except psycopg.errors.UndefinedTable:
                        row = None
                    if row:
                        if row["checksum"] != checksum:
                            raise ConflictError(f"migration checksum changed: {path.name}")
                        continue
                    cursor.execute(path.read_text(encoding="utf-8"))
                    cursor.execute(
                        "INSERT INTO kip.schema_migrations(version, checksum) VALUES (%s, %s) ON CONFLICT (version) DO NOTHING",
                        (version, checksum),
                    )
                applied.append(path.name)
        return applied

    def _ensure_workspace_and_principal(
        self,
        connection: Connection[DictRow],
        context: RequestContext,
    ) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO kip.workspaces(slug, name) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING",
                (context.workspace, context.workspace),
            )
            cursor.execute(
                """
                INSERT INTO kip.principals(id, workspace_id, kind, external_key, display_name)
                VALUES (%s, %s, 'agent', %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    context.principal_id,
                    context.workspace,
                    context.principal_id,
                    context.principal_id,
                ),
            )

    @staticmethod
    def _write_acl_snapshot(
        cursor: Any,
        workspace: str,
        snapshot: AclSnapshot,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO source.acl_snapshots(
                id, workspace_id, provider, snapshot_version, captured_at,
                expires_at, configuration_owned, scopes, scope_mapping
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                snapshot.id,
                workspace,
                snapshot.provider,
                snapshot.version,
                snapshot.captured_at,
                snapshot.expires_at,
                snapshot.configuration_owned,
                snapshot.scopes,
                _json({}),
            ),
        )
        cursor.execute(
            """
            SELECT workspace_id, provider, snapshot_version, captured_at,
                   expires_at, configuration_owned, scopes
            FROM source.acl_snapshots
            WHERE id=%s
            """,
            (snapshot.id,),
        )
        stored = cursor.fetchone()
        if not stored or (
            stored["workspace_id"] != workspace
            or stored["provider"] != snapshot.provider
            or stored["snapshot_version"] != snapshot.version
            or stored["configuration_owned"] != snapshot.configuration_owned
            or list(stored["scopes"] or []) != snapshot.scopes
        ):
            raise ConflictError("ACL snapshot ID was reused with different contents")
        if (
            stored["captured_at"] != snapshot.captured_at
            or stored["expires_at"] != snapshot.expires_at
        ):
            if not snapshot.configuration_owned:
                raise ConflictError(
                    "ACL snapshot ID was reused with different contents"
                )
            # Configuration-owned snapshots regenerate `captured_at` on every
            # scan while their identity (id embeds the policy version) and
            # scopes stay fixed; they are always fresh regardless of
            # `captured_at`, so refreshing the timestamps is an audit update,
            # not an access-policy change. Without this, every re-sync of an
            # unchanged file failed with a snapshot-reuse conflict.
            cursor.execute(
                """
                UPDATE source.acl_snapshots
                SET captured_at=%s, expires_at=%s
                WHERE id=%s
                """,
                (snapshot.captured_at, snapshot.expires_at, snapshot.id),
            )

    def upsert_acl_snapshot(
        self,
        context: RequestContext,
        source_object_id: str,
        snapshot: AclSnapshot,
        classification: DataClassification,
    ) -> None:
        with self._connection(context) as connection:
            self._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                self._write_acl_snapshot(cursor, context.workspace, snapshot)
                cursor.execute(
                    """
                    SELECT acl_snapshot_id, acl_scopes, data_classification
                    FROM source.objects
                    WHERE workspace_id=%s AND id=%s
                    """,
                    (context.workspace, source_object_id),
                )
                current = cursor.fetchone()
                unchanged = bool(
                    current
                    and current["acl_snapshot_id"] == snapshot.id
                    and list(current["acl_scopes"] or []) == list(snapshot.scopes)
                    and current["data_classification"] == classification
                )
                if unchanged:
                    # Snapshot identity, scopes, and classification already
                    # match: refreshing the snapshot row above is enough, so
                    # skip the per-file unit and assertion cascade that would
                    # otherwise rewrite workspace-wide rows on every sync.
                    cursor.execute(
                        """
                        UPDATE source.objects SET last_seen_at=now()
                        WHERE workspace_id=%s AND id=%s
                        """,
                        (context.workspace, source_object_id),
                    )
                    connection.commit()
                    return
                cursor.execute(
                    """
                    UPDATE source.objects
                    SET acl_snapshot_id=%s, acl_scopes=%s,
                        data_classification=%s, last_seen_at=now()
                    WHERE workspace_id=%s AND id=%s
                    """,
                    (
                        snapshot.id,
                        snapshot.scopes,
                        classification,
                        context.workspace,
                        source_object_id,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE content.units unit
                    SET acl_snapshot_id=%s, acl_scopes=%s, data_classification=%s
                    FROM content.artifacts artifact
                    JOIN source.revisions revision ON revision.id=artifact.revision_id
                    WHERE unit.workspace_id=%s
                      AND unit.artifact_id=artifact.id
                      AND revision.object_id=%s
                    """,
                    (
                        snapshot.id,
                        snapshot.scopes,
                        classification,
                        context.workspace,
                        source_object_id,
                    ),
                )
                self._refresh_assertion_acl_snapshots(
                    cursor,
                    context.workspace,
                    source_object_id,
                )
            connection.commit()

    @staticmethod
    def _refresh_assertion_acl_snapshots(
        cursor: Any,
        workspace: str,
        source_object_id: str,
    ) -> None:
        cursor.execute(
            """
            UPDATE knowledge.assertions assertion
            SET evidence_acl_snapshot_ids = (
                SELECT coalesce(
                    array_agg(DISTINCT unit.acl_snapshot_id ORDER BY unit.acl_snapshot_id),
                    ARRAY[]::text[]
                )
                FROM knowledge.assertion_evidence evidence
                JOIN content.units unit ON unit.id=evidence.content_unit_id
                WHERE evidence.workspace_id=assertion.workspace_id
                  AND evidence.assertion_id=assertion.id
            )
            WHERE assertion.workspace_id=%s
              AND EXISTS (
                  SELECT 1
                  FROM knowledge.assertion_evidence evidence
                  JOIN content.units unit ON unit.id=evidence.content_unit_id
                  JOIN content.artifacts artifact ON artifact.id=unit.artifact_id
                  JOIN source.revisions revision ON revision.id=artifact.revision_id
                  WHERE evidence.workspace_id=assertion.workspace_id
                    AND evidence.assertion_id=assertion.id
                    AND revision.object_id=%s
              )
            """,
            (workspace, source_object_id),
        )

    def current_revision_by_stat(
        self,
        context: RequestContext,
        source_object_id: str,
        *,
        size: int,
        mtime_ns: int,
    ) -> str | None:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.id
                FROM source.objects o
                JOIN source.revisions r ON r.id = o.current_revision_id
                WHERE o.workspace_id = %s AND o.id = %s
                  AND NOT r.is_tombstone
                  AND r.size_bytes = %s
                  AND r.metadata->>'mtime_ns' = %s
                """,
                (context.workspace, source_object_id, size, str(mtime_ns)),
            )
            row = cursor.fetchone()
        return str(row["id"]) if row else None

    def has_revision(self, context: RequestContext, source_object_id: str, sha256: str) -> bool:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM source.objects o
                JOIN source.revisions r ON r.id = o.current_revision_id
                WHERE o.workspace_id = %s AND o.id = %s AND r.sha256 = %s
                """,
                (context.workspace, source_object_id, sha256),
            )
            return cursor.fetchone() is not None

    def reconcile_scan_absences(
        self,
        context: RequestContext,
        system_id: str,
        seen_object_ids: AbstractSet[str],
    ) -> list[SourceObjectAbsence]:
        seen = sorted(seen_object_ids)
        with self._connection(context) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE source.objects
                    SET absent_scan_count=0, absent_since=NULL
                    WHERE workspace_id=%s AND system_id=%s
                      AND id = ANY(%s) AND absent_scan_count <> 0
                    """,
                    (context.workspace, system_id, seen),
                )
                cursor.execute(
                    """
                    UPDATE source.objects o
                    SET absent_scan_count = o.absent_scan_count + 1,
                        absent_since = COALESCE(o.absent_since, now())
                    FROM source.revisions r
                    WHERE o.workspace_id=%s AND o.system_id=%s
                      AND r.id = o.current_revision_id
                      AND NOT r.is_tombstone
                      AND NOT (o.id = ANY(%s))
                    RETURNING o.id, o.external_id, o.absent_scan_count,
                              o.current_revision_id
                    """,
                    (context.workspace, system_id, seen),
                )
                marked = cursor.fetchall()
                artifact_by_revision: dict[str, str] = {}
                if marked:
                    cursor.execute(
                        """
                        SELECT id, revision_id
                        FROM content.artifacts
                        WHERE workspace_id=%s AND revision_id = ANY(%s)
                        ORDER BY created_at
                        """,
                        (
                            context.workspace,
                            [row["current_revision_id"] for row in marked],
                        ),
                    )
                    for artifact in cursor.fetchall():
                        artifact_by_revision.setdefault(
                            artifact["revision_id"], artifact["id"]
                        )
            connection.commit()
        return [
            SourceObjectAbsence(
                object_id=row["id"],
                external_id=row["external_id"],
                artifact_id=artifact_by_revision[row["current_revision_id"]],
                absent_scan_count=row["absent_scan_count"],
            )
            for row in marked
            if row["current_revision_id"] in artifact_by_revision
        ]

    def ingest_packet(self, context: RequestContext, packet: DocumentPacket) -> IngestResult:
        if packet.workspace_id != context.workspace:
            raise ValidationError("packet workspace does not match request context")
        snapshot = packet.source_object.acl_snapshot
        if snapshot is None:
            raise ValidationError("source ACL snapshot is required")
        if snapshot.scopes != packet.source_object.acl_scopes:
            raise ValidationError("source ACL scopes must match the ACL snapshot")
        if any(unit.acl_snapshot_id != snapshot.id for unit in packet.units):
            raise ValidationError("every content unit must reference the source ACL snapshot")
        if any(unit.classification != packet.source_object.classification for unit in packet.units):
            raise ValidationError("every content unit must match the source data classification")

        with self._connection(context) as connection:
            self._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                self._write_acl_snapshot(cursor, context.workspace, snapshot)
                cursor.execute(
                    "SELECT current_revision_id FROM source.objects WHERE workspace_id=%s AND id=%s FOR UPDATE",
                    (context.workspace, packet.source_object.id),
                )
                existing_object = cursor.fetchone()
                old_revision_id = (
                    existing_object["current_revision_id"] if existing_object else None
                )
                if old_revision_id:
                    cursor.execute(
                        "SELECT sha256 FROM source.revisions WHERE workspace_id=%s AND id=%s",
                        (context.workspace, old_revision_id),
                    )
                    old_revision = cursor.fetchone()
                    if old_revision and old_revision["sha256"] == packet.revision.sha256:
                        cursor.execute(
                            """
                            UPDATE source.objects
                            SET acl_snapshot_id=%s, acl_scopes=%s,
                                data_classification=%s, last_seen_at=now()
                            WHERE workspace_id=%s AND id=%s
                            """,
                            (
                                snapshot.id,
                                snapshot.scopes,
                                packet.source_object.classification,
                                context.workspace,
                                packet.source_object.id,
                            ),
                        )
                        cursor.execute(
                            """
                            UPDATE content.units unit
                            SET acl_snapshot_id=%s, acl_scopes=%s,
                                data_classification=%s
                            FROM content.artifacts artifact
                            WHERE unit.workspace_id=%s
                              AND unit.artifact_id=artifact.id
                              AND artifact.revision_id=%s
                            """,
                            (
                                snapshot.id,
                                snapshot.scopes,
                                packet.source_object.classification,
                                context.workspace,
                                old_revision_id,
                            ),
                        )
                        self._refresh_assertion_acl_snapshots(
                            cursor,
                            context.workspace,
                            packet.source_object.id,
                        )
                        connection.commit()
                        return IngestResult(
                            status="unchanged",
                            source_object_id=packet.source_object.id,
                            revision_id=old_revision_id,
                            artifact_id=packet.artifact.id,
                            document_id=packet.logical_document.id,
                            extraction_id=packet.extraction.id,
                            unit_count=len(packet.units),
                            warnings=list(packet.extraction.warnings),
                        )

                system_id = packet.source_object.system_id
                cursor.execute(
                    """
                    INSERT INTO source.systems(id, workspace_id, name, kind, metadata)
                    VALUES (%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, kind=EXCLUDED.kind, metadata=EXCLUDED.metadata
                    """,
                    (
                        system_id,
                        context.workspace,
                        packet.source_object.system_name,
                        packet.source_object.system_kind,
                        _json({}),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO source.objects(
                        id, workspace_id, system_id, external_id, object_type, canonical_uri,
                        acl_scopes, acl_snapshot_id, data_classification, metadata,
                        last_seen_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,now())
                    ON CONFLICT (id) DO UPDATE SET
                        canonical_uri=EXCLUDED.canonical_uri,
                        acl_scopes=EXCLUDED.acl_scopes,
                        acl_snapshot_id=EXCLUDED.acl_snapshot_id,
                        data_classification=EXCLUDED.data_classification,
                        metadata=EXCLUDED.metadata,
                        last_seen_at=now(),
                        deleted_at=NULL
                    """,
                    (
                        packet.source_object.id,
                        context.workspace,
                        system_id,
                        packet.source_object.external_id,
                        packet.source_object.object_type,
                        packet.source_object.canonical_uri,
                        packet.source_object.acl_scopes,
                        snapshot.id,
                        packet.source_object.classification,
                        _json(packet.source_object.metadata),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO source.revisions(
                        id, workspace_id, object_id, revision_key, source_modified_at, sha256,
                        size_bytes, raw_object_uri, is_tombstone, metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        packet.revision.id,
                        context.workspace,
                        packet.revision.object_id,
                        packet.revision.revision_key,
                        packet.revision.source_modified_at,
                        packet.revision.sha256,
                        packet.revision.size_bytes,
                        packet.revision.raw_object_uri,
                        packet.revision.is_tombstone,
                        _json(packet.revision.metadata),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO content.logical_documents(
                        id, workspace_id, stable_key, title, document_type, family_key, lifecycle, metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (id) DO UPDATE SET
                        title=EXCLUDED.title,
                        document_type=EXCLUDED.document_type,
                        family_key=EXCLUDED.family_key,
                        lifecycle=EXCLUDED.lifecycle,
                        metadata=EXCLUDED.metadata
                    """,
                    (
                        packet.logical_document.id,
                        context.workspace,
                        packet.logical_document.stable_key,
                        packet.logical_document.title,
                        packet.logical_document.document_type,
                        packet.logical_document.family_key,
                        packet.logical_document.lifecycle,
                        _json(packet.logical_document.metadata),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO content.artifacts(
                        id, workspace_id, revision_id, file_name, extension, media_type, byte_size,
                        sha256, source_path, cas_uri, representation_role, metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        packet.artifact.id,
                        context.workspace,
                        packet.artifact.revision_id,
                        packet.artifact.file_name,
                        packet.artifact.extension,
                        packet.artifact.media_type,
                        packet.artifact.byte_size,
                        packet.artifact.sha256,
                        packet.artifact.source_path,
                        packet.artifact.cas_uri,
                        packet.artifact.representation_role,
                        _json(packet.artifact.metadata),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO content.document_artifacts(
                        workspace_id, document_id, artifact_id, representation_role, is_primary
                    ) VALUES (%s,%s,%s,%s,true)
                    ON CONFLICT (workspace_id, document_id, artifact_id) DO UPDATE SET
                        representation_role=EXCLUDED.representation_role,
                        is_primary=true
                    """,
                    (
                        context.workspace,
                        packet.logical_document.id,
                        packet.artifact.id,
                        packet.artifact.representation_role or "primary",
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO content.extractions(
                        id, workspace_id, artifact_id, parser_name, parser_version, status, active,
                        quality_score, output_hash, warnings, completed_at, metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,false,%s,%s,%s::jsonb,now(),%s::jsonb)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        packet.extraction.id,
                        context.workspace,
                        packet.extraction.artifact_id,
                        packet.extraction.parser_name,
                        packet.extraction.parser_version,
                        packet.extraction.status,
                        packet.extraction.quality_score,
                        packet.extraction.output_hash,
                        _json(packet.extraction.warnings),
                        _json(packet.extraction.metadata),
                    ),
                )

                for unit in packet.units:
                    cursor.execute(
                        """
                        INSERT INTO content.units(
                            id, workspace_id, extraction_id, document_id, artifact_id, ordinal, unit_type,
                            title, body, body_normalized, lexical_text, locator, acl_scopes, acl_snapshot_id,
                            data_classification, char_count, metadata
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            unit.id,
                            context.workspace,
                            unit.extraction_id,
                            unit.document_id,
                            unit.artifact_id,
                            unit.ordinal,
                            unit.unit_type,
                            unit.title,
                            unit.body,
                            unit.body_normalized,
                            unit.lexical_text,
                            _json(unit.locator.model_dump(mode="json")),
                            unit.acl_scopes,
                            unit.acl_snapshot_id,
                            unit.classification,
                            len(unit.body),
                            _json(unit.metadata),
                        ),
                    )
                    cursor.execute(
                        """
                        INSERT INTO search.lexical_units(
                            unit_id, workspace_id, document_id, artifact_id, source_kind,
                            title, body, lexemes, identifier_text, source_modified_at, source_sha256
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (unit_id) DO UPDATE SET
                            title=EXCLUDED.title,
                            body=EXCLUDED.body,
                            lexemes=EXCLUDED.lexemes,
                            identifier_text=EXCLUDED.identifier_text,
                            source_modified_at=EXCLUDED.source_modified_at,
                            source_sha256=EXCLUDED.source_sha256,
                            updated_at=now()
                        """,
                        (
                            unit.id,
                            context.workspace,
                            unit.document_id,
                            unit.artifact_id,
                            packet.source_object.system_kind,
                            unit.title or packet.logical_document.title,
                            unit.body,
                            unit.lexical_text,
                            " ".join(
                                filter(
                                    None,
                                    [
                                        packet.artifact.file_name,
                                        packet.logical_document.title,
                                        str(
                                            packet.source_object.metadata.get("document_number", "")
                                        ),
                                        str(packet.logical_document.metadata.get("project_id", "")),
                                    ],
                                )
                            ),
                            packet.revision.source_modified_at,
                            packet.revision.sha256,
                        ),
                    )

                if old_revision_id:
                    cursor.execute(
                        """
                        UPDATE content.extractions SET active=false
                        WHERE workspace_id=%s AND artifact_id IN (
                            SELECT id FROM content.artifacts WHERE workspace_id=%s AND revision_id=%s
                        )
                        """,
                        (context.workspace, context.workspace, old_revision_id),
                    )
                    cursor.execute(
                        """
                        DELETE FROM search.lexical_units l
                        USING content.units u, content.artifacts a
                        WHERE l.unit_id=u.id AND u.artifact_id=a.id
                          AND l.workspace_id=%s AND a.revision_id=%s
                        """,
                        (context.workspace, old_revision_id),
                    )

                cursor.execute(
                    "UPDATE content.extractions SET active=true WHERE workspace_id=%s AND id=%s",
                    (context.workspace, packet.extraction.id),
                )
                cursor.execute(
                    "UPDATE source.objects SET current_revision_id=%s WHERE workspace_id=%s AND id=%s",
                    (packet.revision.id, context.workspace, packet.source_object.id),
                )
                cursor.execute(
                    """
                    INSERT INTO audit.events(public_id, workspace_id, actor_id, action, object_type, object_id, request_id, details)
                    VALUES (%s,%s,%s,'ingest','source_object',%s,%s,%s::jsonb)
                    """,
                    (
                        new_id("audit"),
                        context.workspace,
                        context.principal_id,
                        packet.source_object.id,
                        context.request_id,
                        _json({"revision_id": packet.revision.id, "unit_count": len(packet.units)}),
                    ),
                )
            connection.commit()

        return IngestResult(
            status="replaced" if old_revision_id else "inserted",
            source_object_id=packet.source_object.id,
            revision_id=packet.revision.id,
            artifact_id=packet.artifact.id,
            document_id=packet.logical_document.id,
            extraction_id=packet.extraction.id,
            unit_count=len(packet.units),
            warnings=list(packet.extraction.warnings),
        )

    def replace_extraction(
        self,
        context: RequestContext,
        packet: DocumentPacket,
    ) -> IngestResult:
        if packet.workspace_id != context.workspace:
            raise ValidationError("packet workspace does not match request context")
        snapshot = packet.source_object.acl_snapshot
        if snapshot is None:
            raise ValidationError("source ACL snapshot is required")
        if packet.extraction.status == "failed" or not packet.units:
            raise ValidationError("only a usable extraction can be activated")
        if packet.revision.object_id != packet.source_object.id:
            raise ValidationError("revision does not reference the source object")
        if packet.artifact.revision_id != packet.revision.id:
            raise ValidationError("artifact does not reference the source revision")
        if packet.extraction.artifact_id != packet.artifact.id:
            raise ValidationError("extraction does not reference the artifact")
        if any(
            unit.extraction_id != packet.extraction.id
            or unit.artifact_id != packet.artifact.id
            or unit.document_id != packet.logical_document.id
            for unit in packet.units
        ):
            raise ValidationError("candidate units do not reference the candidate extraction")
        if len({unit.ordinal for unit in packet.units}) != len(packet.units):
            raise ValidationError("candidate unit ordinals must be unique")
        if any(
            unit.acl_snapshot_id != snapshot.id
            or unit.acl_scopes != snapshot.scopes
            or unit.classification != packet.source_object.classification
            for unit in packet.units
        ):
            raise ValidationError("candidate units do not preserve source access controls")

        with self._connection(context) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        object.current_revision_id,
                        object.acl_snapshot_id,
                        object.acl_scopes,
                        object.data_classification,
                        system.kind AS system_kind,
                        revision.sha256 AS revision_sha256,
                        artifact.revision_id AS artifact_revision_id,
                        artifact.sha256 AS artifact_sha256,
                        document_artifact.document_id
                    FROM source.objects object
                    JOIN source.systems system ON system.id=object.system_id
                    JOIN source.revisions revision
                      ON revision.id=object.current_revision_id
                     AND revision.workspace_id=object.workspace_id
                    JOIN content.artifacts artifact
                      ON artifact.workspace_id=object.workspace_id
                     AND artifact.revision_id=revision.id
                     AND artifact.id=%s
                    LEFT JOIN content.document_artifacts document_artifact
                      ON document_artifact.workspace_id=artifact.workspace_id
                     AND document_artifact.artifact_id=artifact.id
                     AND document_artifact.is_primary
                    WHERE object.workspace_id=%s AND object.id=%s
                    FOR UPDATE OF object
                    """,
                    (
                        packet.artifact.id,
                        context.workspace,
                        packet.source_object.id,
                    ),
                )
                current = cursor.fetchone()
                if current is None or (
                    current["current_revision_id"] != packet.revision.id
                    or current["revision_sha256"] != packet.revision.sha256
                    or current["artifact_revision_id"] != packet.revision.id
                    or current["artifact_sha256"] != packet.artifact.sha256
                    or current["document_id"] != packet.logical_document.id
                ):
                    raise ConflictError("candidate does not match the current source revision")
                if (
                    current["acl_snapshot_id"] != snapshot.id
                    or list(current["acl_scopes"] or []) != snapshot.scopes
                    or current["data_classification"] != packet.source_object.classification
                ):
                    raise ConflictError("candidate access controls do not match the current source")

                cursor.execute(
                    """
                    INSERT INTO content.extractions(
                        id, workspace_id, artifact_id, parser_name, parser_version,
                        status, active, quality_score, output_hash, warnings,
                        completed_at, metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,false,%s,%s,%s::jsonb,now(),%s::jsonb)
                    """,
                    (
                        packet.extraction.id,
                        context.workspace,
                        packet.artifact.id,
                        packet.extraction.parser_name,
                        packet.extraction.parser_version,
                        packet.extraction.status,
                        packet.extraction.quality_score,
                        packet.extraction.output_hash,
                        _json(packet.extraction.warnings),
                        _json(packet.extraction.metadata),
                    ),
                )
                for unit in packet.units:
                    cursor.execute(
                        """
                        INSERT INTO content.units(
                            id, workspace_id, extraction_id, document_id, artifact_id,
                            ordinal, unit_type, title, body, body_normalized,
                            lexical_text, locator, acl_scopes, acl_snapshot_id,
                            data_classification, char_count, metadata
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb)
                        """,
                        (
                            unit.id,
                            context.workspace,
                            unit.extraction_id,
                            unit.document_id,
                            unit.artifact_id,
                            unit.ordinal,
                            unit.unit_type,
                            unit.title,
                            unit.body,
                            unit.body_normalized,
                            unit.lexical_text,
                            _json(unit.locator.model_dump(mode="json")),
                            unit.acl_scopes,
                            unit.acl_snapshot_id,
                            unit.classification,
                            len(unit.body),
                            _json(unit.metadata),
                        ),
                    )

                cursor.execute(
                    """
                    DELETE FROM search.lexical_units lexical
                    USING content.units unit, content.extractions extraction
                    WHERE lexical.unit_id=unit.id
                      AND unit.extraction_id=extraction.id
                      AND extraction.workspace_id=%s
                      AND extraction.artifact_id=%s
                      AND extraction.active
                    """,
                    (context.workspace, packet.artifact.id),
                )
                cursor.execute(
                    """
                    UPDATE content.extractions
                    SET active=false
                    WHERE workspace_id=%s AND artifact_id=%s AND active
                    """,
                    (context.workspace, packet.artifact.id),
                )
                identifier_text = " ".join(
                    filter(
                        None,
                        [
                            packet.artifact.file_name,
                            packet.logical_document.title,
                            str(packet.source_object.metadata.get("document_number", "")),
                            str(packet.logical_document.metadata.get("project_id", "")),
                        ],
                    )
                )
                for unit in packet.units:
                    cursor.execute(
                        """
                        INSERT INTO search.lexical_units(
                            unit_id, workspace_id, document_id, artifact_id,
                            source_kind, title, body, lexemes, identifier_text,
                            source_modified_at, source_sha256
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            unit.id,
                            context.workspace,
                            unit.document_id,
                            unit.artifact_id,
                            current["system_kind"],
                            unit.title or packet.logical_document.title,
                            unit.body,
                            unit.lexical_text,
                            identifier_text,
                            packet.revision.source_modified_at,
                            packet.revision.sha256,
                        ),
                    )
                cursor.execute(
                    """
                    UPDATE content.extractions
                    SET active=true
                    WHERE workspace_id=%s AND id=%s
                    """,
                    (context.workspace, packet.extraction.id),
                )
                cursor.execute(
                    """
                    INSERT INTO audit.events(
                        public_id, workspace_id, actor_id, action, object_type,
                        object_id, request_id, details
                    ) VALUES (
                        %s,%s,%s,'extraction.activate','artifact',%s,%s,%s::jsonb
                    )
                    """,
                    (
                        new_id("audit"),
                        context.workspace,
                        context.principal_id,
                        packet.artifact.id,
                        context.request_id,
                        _json(
                            {
                                "extraction_id": packet.extraction.id,
                                "parser_name": packet.extraction.parser_name,
                                "quality_score": packet.extraction.quality_score,
                                "unit_count": len(packet.units),
                            }
                        ),
                    ),
                )
            connection.commit()

        return IngestResult(
            status="replaced",
            source_object_id=packet.source_object.id,
            revision_id=packet.revision.id,
            artifact_id=packet.artifact.id,
            document_id=packet.logical_document.id,
            extraction_id=packet.extraction.id,
            unit_count=len(packet.units),
            warnings=list(packet.extraction.warnings),
        )

    def search(
        self, context: RequestContext, request: SearchRequest, lexemes: str
    ) -> list[SearchHit]:
        websearch_query = _websearch_or_query(lexemes)
        inner_conditions = ["l.workspace_id=%s"]
        inner_condition_params: list[Any] = [context.workspace]
        if request.source_kinds:
            inner_conditions.append("l.source_kind = ANY(%s::text[])")
            inner_condition_params.append(request.source_kinds)
        outer_conditions = [
            "(cardinality(u.acl_scopes)=0 OR u.acl_scopes <@ %s::text[])",
            "kip.acl_snapshot_is_fresh(u.acl_snapshot_id)",
        ]
        outer_condition_params: list[Any] = [context.acl_scopes]
        if request.document_types:
            outer_conditions.append("d.document_type = ANY(%s::text[])")
            outer_condition_params.append(request.document_types)
        if request.project_ids:
            outer_conditions.append("coalesce(d.metadata->>'project_id','') = ANY(%s::text[])")
            outer_condition_params.append(request.project_ids)

        # Every OR arm stays in an index-usable form (ILIKE and the %% trigram
        # operator hit the gin_trgm_ops indexes, tsv hits its GIN index). The
        # MATERIALIZED CTE forces one scan of lexical_units for the match set;
        # without the fence the planner has re-scanned the bitmap once per
        # artifact row under misestimation. ACL and freshness filters still
        # run before ORDER BY/LIMIT, and is_latest is computed only for the
        # LIMITed rows.
        sql = f"""
            SELECT
                q.*,
                coalesce(
                    q.revision_modified_at >= (
                        SELECT max(r2.source_modified_at)
                        FROM content.document_artifacts da2
                        JOIN content.artifacts a2 ON a2.id=da2.artifact_id
                        JOIN source.revisions r2 ON r2.id=a2.revision_id
                        JOIN source.objects o2
                          ON o2.id=r2.object_id AND o2.current_revision_id=r2.id
                        WHERE da2.workspace_id=%s
                          AND da2.document_id=q.document_id
                    ),
                    true
                ) AS is_latest
            FROM (
            WITH matched AS MATERIALIZED (
                SELECT
                    l.unit_id, l.document_id, l.artifact_id, l.source_kind,
                    l.title, l.source_sha256, l.source_modified_at,
                    left(regexp_replace(l.body, '\\s+', ' ', 'g'), 500) AS snippet,
                    (
                        CASE WHEN lower(l.identifier_text) = lower(%s) THEN 30 ELSE 0 END
                      + CASE WHEN l.title ILIKE '%%' || %s || '%%' THEN 10 ELSE 0 END
                      + CASE WHEN l.body ILIKE '%%' || %s || '%%' THEN 6 ELSE 0 END
                      + CASE WHEN l.tsv @@ websearch_to_tsquery('simple', %s) THEN ts_rank_cd(l.tsv, websearch_to_tsquery('simple', %s)) * 10 ELSE 0 END
                      + similarity(l.title, %s) * 2
                    ) AS score
                FROM search.lexical_units l
                WHERE {" AND ".join(inner_conditions)}
                  AND (
                        l.identifier_text ILIKE '%%' || %s || '%%'
                     OR l.title ILIKE '%%' || %s || '%%'
                     OR l.body ILIKE '%%' || %s || '%%'
                     OR l.tsv @@ websearch_to_tsquery('simple', %s)
                     OR l.title %% %s
                  )
            )
            SELECT
                m.unit_id, m.document_id, m.artifact_id, m.source_kind,
                m.title, m.snippet, m.score,
                u.locator, o.canonical_uri AS source_uri, m.source_sha256,
                m.source_modified_at, a.file_name, d.document_type,
                r.source_modified_at AS revision_modified_at
            FROM matched m
            JOIN content.units u ON u.id=m.unit_id
            JOIN content.artifacts a ON a.id=m.artifact_id
            JOIN source.revisions r ON r.id=a.revision_id
            JOIN source.objects o ON o.id=r.object_id AND o.current_revision_id=r.id
            LEFT JOIN content.logical_documents d ON d.id=m.document_id
            WHERE {" AND ".join(outer_conditions)}
            ORDER BY m.score DESC, m.unit_id
            LIMIT %s
            ) q
            ORDER BY q.score DESC, q.unit_id
        """
        score_params = [
            request.query,
            request.query,
            request.query,
            websearch_query,
            websearch_query,
            request.query,
        ]
        or_params = [
            request.query,
            request.query,
            request.query,
            websearch_query,
            request.query,
        ]
        all_params = [
            context.workspace,
            *score_params,
            *inner_condition_params,
            *or_params,
            *outer_condition_params,
            request.limit,
        ]

        with self._connection(context) as connection, connection.cursor() as cursor:
            # Preserve the historical similarity cutoff for the trigram %%
            # operator, which reads this transaction-local GUC.
            cursor.execute("SELECT set_config('pg_trgm.similarity_threshold', '0.15', true)")
            cursor.execute(sql, all_params)
            rows = cursor.fetchall()
        return [
            SearchHit(
                unit_id=row["unit_id"],
                document_id=row["document_id"],
                artifact_id=row["artifact_id"],
                source_kind=row["source_kind"],
                title=row["title"],
                snippet=row["snippet"],
                score=float(row["score"] or 0),
                locator=EvidenceLocator.model_validate(row["locator"]),
                source_uri=row["source_uri"],
                source_sha256=row["source_sha256"],
                source_modified_at=row["source_modified_at"],
                metadata={
                    "file_name": row["file_name"],
                    "document_type": row["document_type"],
                    "is_latest": bool(row["is_latest"]),
                },
            )
            for row in rows
        ]

    def list_embeddable_units(self, context: RequestContext) -> list[EmbeddableUnit]:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    u.id AS unit_id,
                    u.document_id,
                    coalesce(u.title, d.title, '') AS title,
                    u.body_normalized,
                    r.sha256 AS source_hash
                FROM content.units u
                JOIN content.extractions e ON e.id=u.extraction_id AND e.active
                JOIN content.artifacts a ON a.id=u.artifact_id
                JOIN source.revisions r ON r.id=a.revision_id
                JOIN source.objects o ON o.id=r.object_id AND o.current_revision_id=r.id
                LEFT JOIN content.logical_documents d ON d.id=u.document_id
                WHERE u.workspace_id=%s
                  AND (cardinality(u.acl_scopes)=0 OR u.acl_scopes <@ %s::text[])
                  AND kip.acl_snapshot_is_fresh(u.acl_snapshot_id)
                ORDER BY u.id
                """,
                (context.workspace, context.acl_scopes),
            )
            rows = cursor.fetchall()
        return [EmbeddableUnit.model_validate(row) for row in rows]

    @staticmethod
    def _embedding_space(row: dict[str, Any]) -> EmbeddingSpace:
        configuration = dict(row["configuration"] or {})
        return EmbeddingSpace(
            id=row["id"],
            name=row["name"],
            provider=row["provider"],
            model=row["model"],
            revision=row["model_revision"],
            dimensions=row["dimensions"],
            normalized=row["normalized"],
            status=row["status"],
            configuration=configuration,
        )

    def save_embedding_space(
        self,
        context: RequestContext,
        space: EmbeddingSpace,
    ) -> EmbeddingSpace:
        # Validates that a provisioned embeddings table exists for this
        # dimensionality; the table itself is not needed here.
        _embeddings_table(space.dimensions)
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO search.embedding_spaces(
                    id,workspace_id,name,provider,model,model_revision,dimensions,
                    distance_metric,status,normalized,configuration
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,'cosine',%s,%s,%s::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    provider=EXCLUDED.provider,
                    model=EXCLUDED.model,
                    model_revision=EXCLUDED.model_revision,
                    dimensions=EXCLUDED.dimensions,
                    status=CASE
                        WHEN search.embedding_spaces.status='active' THEN 'active'
                        ELSE EXCLUDED.status
                    END,
                    normalized=EXCLUDED.normalized,
                    configuration=EXCLUDED.configuration
                RETURNING *
                """,
                (
                    space.id,
                    context.workspace,
                    space.name,
                    space.provider,
                    space.model,
                    space.revision,
                    space.dimensions,
                    space.status,
                    space.normalized,
                    _json(space.configuration),
                ),
            )
            row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise DependencyUnavailableError("PostgreSQL did not return the saved embedding space")
        return self._embedding_space(row)

    def active_embedding_space(self, context: RequestContext) -> EmbeddingSpace | None:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM search.embedding_spaces
                WHERE workspace_id=%s AND status='active'
                """,
                (context.workspace,),
            )
            row = cursor.fetchone()
        return self._embedding_space(row) if row else None

    def activate_embedding_space(
        self,
        context: RequestContext,
        space_id: str,
    ) -> EmbeddingSpace:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM search.embedding_spaces
                WHERE workspace_id=%s AND id=%s
                FOR UPDATE
                """,
                (context.workspace, space_id),
            )
            if not cursor.fetchone():
                raise NotFoundError(f"embedding space not found: {space_id}")
            cursor.execute(
                """
                UPDATE search.embedding_spaces
                SET status='shadow'
                WHERE workspace_id=%s AND status='active' AND id<>%s
                """,
                (context.workspace, space_id),
            )
            cursor.execute(
                """
                UPDATE search.embedding_spaces
                SET status='active'
                WHERE workspace_id=%s AND id=%s
                RETURNING *
                """,
                (context.workspace, space_id),
            )
            row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise DependencyUnavailableError(
                "PostgreSQL did not return the activated embedding space"
            )
        return self._embedding_space(row)

    def upsert_embeddings(
        self,
        context: RequestContext,
        space_id: str,
        records: list[EmbeddingRecord],
    ) -> int:
        if not records:
            return 0
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT dimensions
                FROM search.embedding_spaces
                WHERE workspace_id=%s AND id=%s
                """,
                (context.workspace, space_id),
            )
            row = cursor.fetchone()
            if not row:
                raise NotFoundError(f"embedding space not found: {space_id}")
            dimensions = int(row["dimensions"])
            table = _embeddings_table(dimensions)
            values = []
            for record in records:
                if len(record.embedding) != dimensions:
                    raise ValidationError(
                        f"embedding dimension {len(record.embedding)} does not match {dimensions}"
                    )
                values.append(
                    (
                        context.workspace,
                        record.unit_id,
                        space_id,
                        _vector_literal(record.embedding),
                        record.source_hash,
                    )
                )
            cursor.executemany(
                f"""
                INSERT INTO {table}(
                    workspace_id,unit_id,space_id,embedding,source_hash
                ) VALUES (%s,%s,%s,%s::vector,%s)
                ON CONFLICT (workspace_id,unit_id,space_id) DO UPDATE SET
                    embedding=EXCLUDED.embedding,
                    source_hash=EXCLUDED.source_hash,
                    updated_at=now()
                """,
                values,
            )
            connection.commit()
        return len(records)

    def vector_search(
        self,
        context: RequestContext,
        request: SearchRequest,
        query_embedding: list[float],
        *,
        space_id: str,
        limit: int,
    ) -> list[SearchHit]:
        table = _embeddings_table(len(query_embedding))
        eligibility_conditions = [
            "u.id=v.unit_id",
            "v.source_hash=r.sha256",
            "(cardinality(u.acl_scopes)=0 OR u.acl_scopes <@ %s::text[])",
            "kip.acl_snapshot_is_fresh(u.acl_snapshot_id)",
        ]
        eligibility_params: list[Any] = [context.acl_scopes]
        if request.source_kinds:
            eligibility_conditions.append("l.source_kind = ANY(%s::text[])")
            eligibility_params.append(request.source_kinds)
        if request.document_types:
            eligibility_conditions.append("d.document_type = ANY(%s::text[])")
            eligibility_params.append(request.document_types)
        if request.project_ids:
            eligibility_conditions.append(
                "coalesce(d.metadata->>'project_id','') = ANY(%s::text[])"
            )
            eligibility_params.append(request.project_ids)
        vector = _vector_literal(query_embedding)
        sql = f"""
            WITH nearest AS MATERIALIZED (
                SELECT
                    v.unit_id,
                    v.embedding <=> %s::vector AS distance
                FROM {table} v
                WHERE v.workspace_id=%s
                  AND v.space_id=%s
                  AND EXISTS (
                      SELECT 1
                      FROM content.units u
                      JOIN search.lexical_units l ON l.unit_id=u.id
                      JOIN content.artifacts a ON a.id=l.artifact_id
                      JOIN source.revisions r ON r.id=a.revision_id
                      JOIN source.objects o
                        ON o.id=r.object_id AND o.current_revision_id=r.id
                      LEFT JOIN content.logical_documents d
                        ON d.id=l.document_id
                      WHERE {" AND ".join(eligibility_conditions)}
                      OFFSET 0
                  )
                ORDER BY v.embedding <=> %s::vector
                LIMIT %s
            )
            SELECT
                l.unit_id,l.document_id,l.artifact_id,l.source_kind,l.title,
                left(regexp_replace(l.body, '\\s+', ' ', 'g'), 500) AS snippet,
                1 - n.distance AS score,
                u.locator,o.canonical_uri AS source_uri,l.source_sha256,
                l.source_modified_at,a.file_name,d.document_type,
                r.source_modified_at AS revision_modified_at,
                coalesce(
                    r.source_modified_at >= (
                        SELECT max(r2.source_modified_at)
                        FROM content.document_artifacts da2
                        JOIN content.artifacts a2 ON a2.id=da2.artifact_id
                        JOIN source.revisions r2 ON r2.id=a2.revision_id
                        JOIN source.objects o2
                          ON o2.id=r2.object_id AND o2.current_revision_id=r2.id
                        WHERE da2.workspace_id=%s
                          AND da2.document_id=l.document_id
                    ),
                    true
                ) AS is_latest
            FROM nearest n
            JOIN content.units u ON u.id=n.unit_id
            JOIN search.lexical_units l ON l.unit_id=u.id
            JOIN content.artifacts a ON a.id=l.artifact_id
            JOIN source.revisions r ON r.id=a.revision_id
            JOIN source.objects o ON o.id=r.object_id AND o.current_revision_id=r.id
            LEFT JOIN content.logical_documents d ON d.id=l.document_id
            ORDER BY n.distance, n.unit_id
        """
        params = [
            vector,
            context.workspace,
            space_id,
            *eligibility_params,
            vector,
            limit,
            context.workspace,
        ]
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('hnsw.ef_search', %s, true),"
                " set_config('hnsw.iterative_scan', 'strict_order', true),"
                " set_config('hnsw.max_scan_tuples', %s, true)",
                (
                    str(self.hnsw_ef_search),
                    str(self.hnsw_max_scan_tuples),
                ),
            )
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [
            SearchHit(
                unit_id=row["unit_id"],
                document_id=row["document_id"],
                artifact_id=row["artifact_id"],
                source_kind=row["source_kind"],
                title=row["title"],
                snippet=row["snippet"],
                score=float(row["score"]),
                locator=EvidenceLocator.model_validate(row["locator"]),
                source_uri=row["source_uri"],
                source_sha256=row["source_sha256"],
                source_modified_at=row["source_modified_at"],
                metadata={
                    "file_name": row["file_name"],
                    "document_type": row["document_type"],
                    "retrieval_channels": ["vector"],
                    "vector_rank": rank,
                    "is_latest": bool(row["is_latest"]),
                },
            )
            for rank, row in enumerate(rows, start=1)
        ]

    def semantic_status(self, context: RequestContext) -> dict[str, Any]:
        # A workspace can have coexisting spaces of different dimensions
        # (each backed by its own provisioned table), so the vector count is
        # a UNION ALL across every provisioned table rather than one fixed
        # table.
        embeddings_union = _embeddings_union_sql("workspace_id, space_id, unit_id")
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT s.id, s.status, count(e.unit_id)::int AS vectors
                FROM search.embedding_spaces s
                LEFT JOIN ({embeddings_union}) e
                  ON e.workspace_id=s.workspace_id AND e.space_id=s.id
                WHERE s.workspace_id=%s
                GROUP BY s.id, s.status
                """,
                (context.workspace,),
            )
            rows = cursor.fetchall()
        space_vectors = {str(row["id"]): int(row["vectors"]) for row in rows}
        active = self.active_embedding_space(context)
        return {
            "spaces": len(rows),
            "vectors": sum(space_vectors.values()),
            "active_space": active.model_dump(mode="json") if active else None,
            "space_vectors": space_vectors,
            "space_status": {str(row["id"]): str(row["status"]) for row in rows},
        }

    def vocabulary(
        self, context: RequestContext, prefix: str, limit: int = 20
    ) -> list[VocabularyItem]:
        normalized = prefix.strip().lower()
        if not normalized:
            raise ValidationError("vocabulary prefix must not be blank")
        if len(normalized.split()) > 1:
            raise ValidationError("vocabulary prefix must be a single term")
        # Restrict the lexeme unnest to units that already contain a token
        # with this prefix. Unnesting every unit first materialized roughly
        # (units x n-grams) rows and exceeded the statement timeout for
        # short Hangul prefixes.
        tsquery = "'" + normalized.replace("'", "").replace("\\", "") + "':*"
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT token AS term, count(DISTINCT unit_id)::int AS document_frequency, count(*)::int AS corpus_frequency
                FROM search.lexical_units l
                JOIN content.units u ON u.id=l.unit_id
                CROSS JOIN LATERAL unnest(string_to_array(l.lexemes, ' ')) AS token
                WHERE l.workspace_id=%s
                  AND (cardinality(u.acl_scopes)=0 OR u.acl_scopes <@ %s::text[])
                  AND kip.acl_snapshot_is_fresh(u.acl_snapshot_id)
                  AND l.tsv @@ to_tsquery('simple', %s)
                  AND token <> ''
                  AND token LIKE %s || '%%'
                GROUP BY token
                ORDER BY document_frequency DESC, corpus_frequency DESC, token
                LIMIT %s
                """,
                (context.workspace, context.acl_scopes, tsquery, normalized, limit),
            )
            rows = cursor.fetchall()
        return [VocabularyItem(**dict(row)) for row in rows]

    def term_document_frequencies(
        self,
        context: RequestContext,
        terms: list[str],
    ) -> dict[str, int]:
        # ACL-filtered count of documents whose lexical projection contains
        # each whole term. Used by the abstention gate to decide whether the
        # query's vocabulary exists in the reachable corpus at all.
        cleaned = [term for term in dict.fromkeys(terms) if term]
        if not cleaned:
            return {}
        with self._connection(context) as connection, connection.cursor() as cursor:
            frequencies: dict[str, int] = {}
            for term in cleaned:
                tsquery = "'" + term.replace("'", "").replace("\\", "") + "'"
                cursor.execute(
                    """
                    SELECT count(DISTINCT l.document_id)::int AS df
                    FROM search.lexical_units l
                    JOIN content.units u ON u.id=l.unit_id
                    WHERE l.workspace_id=%s
                      AND (cardinality(u.acl_scopes)=0 OR u.acl_scopes <@ %s::text[])
                      AND kip.acl_snapshot_is_fresh(u.acl_snapshot_id)
                      AND l.tsv @@ to_tsquery('simple', %s)
                    """,
                    (context.workspace, context.acl_scopes, tsquery),
                )
                row = cursor.fetchone()
                frequencies[term] = int(row["df"]) if row else 0
        return frequencies

    def get_content_units(
        self, context: RequestContext, unit_ids: Sequence[str]
    ) -> list[ContentUnit]:
        if not unit_ids:
            return []
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.*, l.lexemes
                FROM content.units u
                LEFT JOIN search.lexical_units l ON l.unit_id=u.id
                WHERE u.workspace_id=%s AND u.id = ANY(%s::text[])
                  AND (cardinality(u.acl_scopes)=0 OR u.acl_scopes <@ %s::text[])
                  AND kip.acl_snapshot_is_fresh(u.acl_snapshot_id)
                """,
                (context.workspace, list(unit_ids), context.acl_scopes),
            )
            rows = cursor.fetchall()
        by_id = {
            row["id"]: ContentUnit(
                id=row["id"],
                extraction_id=row["extraction_id"],
                document_id=row["document_id"],
                artifact_id=row["artifact_id"],
                ordinal=row["ordinal"],
                unit_type=row["unit_type"],
                title=row["title"],
                body=row["body"],
                body_normalized=row["body_normalized"],
                lexical_text=row["lexical_text"] or row["lexemes"] or row["body_normalized"],
                locator=EvidenceLocator.model_validate(row["locator"]),
                acl_scopes=list(row["acl_scopes"] or []),
                acl_snapshot_id=row["acl_snapshot_id"],
                classification=row["data_classification"],
                metadata=row["metadata"] or {},
            )
            for row in rows
        }
        missing = [unit_id for unit_id in unit_ids if unit_id not in by_id]
        if missing:
            raise NotFoundError(f"content unit not found: {missing[0]}")
        return [by_id[unit_id] for unit_id in unit_ids]

    def get_content_unit(self, context: RequestContext, unit_id: str) -> ContentUnit:
        return self.get_content_units(context, [unit_id])[0]

    def get_artifact(self, context: RequestContext, artifact_id: str) -> ArtifactView:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    a.*, r.object_id, r.revision_key, r.sha256 AS revision_sha256,
                    r.size_bytes AS revision_size_bytes, r.source_modified_at, r.raw_object_uri,
                    r.is_tombstone, r.metadata AS revision_metadata,
                    o.system_id, o.external_id, o.object_type, o.canonical_uri,
                    o.acl_scopes AS object_acl_scopes, o.metadata AS object_metadata,
                    o.data_classification AS object_data_classification,
                    snapshot.id AS acl_snapshot_id,
                    snapshot.provider AS acl_snapshot_provider,
                    snapshot.snapshot_version AS acl_snapshot_version,
                    snapshot.captured_at AS acl_snapshot_captured_at,
                    snapshot.expires_at AS acl_snapshot_expires_at,
                    snapshot.configuration_owned AS acl_snapshot_configuration_owned,
                    snapshot.scopes AS acl_snapshot_scopes,
                    s.name AS system_name, s.kind AS system_kind,
                    d.id AS document_id, d.stable_key, d.title AS document_title,
                    d.document_type, d.family_key, d.lifecycle, d.metadata AS document_metadata
                FROM content.artifacts a
                JOIN source.revisions r ON r.id=a.revision_id
                JOIN source.objects o ON o.id=r.object_id AND o.current_revision_id=r.id
                JOIN source.acl_snapshots snapshot ON snapshot.id=o.acl_snapshot_id
                JOIN source.systems s ON s.id=o.system_id
                LEFT JOIN content.document_artifacts da ON da.artifact_id=a.id
                LEFT JOIN content.logical_documents d ON d.id=da.document_id
                WHERE a.workspace_id=%s AND a.id=%s
                  AND (cardinality(o.acl_scopes)=0 OR o.acl_scopes <@ %s::text[])
                  AND kip.acl_snapshot_is_fresh(o.acl_snapshot_id)
                """,
                (context.workspace, artifact_id, context.acl_scopes),
            )
            row = cursor.fetchone()
        if not row:
            raise NotFoundError(f"artifact not found: {artifact_id}")
        artifact = Artifact(
            id=row["id"],
            revision_id=row["revision_id"],
            file_name=row["file_name"],
            extension=row["extension"],
            media_type=row["media_type"],
            byte_size=row["byte_size"],
            sha256=row["sha256"],
            source_path=row["source_path"],
            cas_uri=row["cas_uri"],
            representation_role=row["representation_role"],
            metadata=row["metadata"] or {},
        )
        document = None
        if row["document_id"]:
            document = LogicalDocument(
                id=row["document_id"],
                stable_key=row["stable_key"],
                title=row["document_title"],
                document_type=row["document_type"],
                family_key=row["family_key"],
                lifecycle=row["lifecycle"],
                metadata=row["document_metadata"] or {},
            )
        source_object = SourceObject(
            id=row["object_id"],
            system_id=row["system_id"],
            system_name=row["system_name"],
            system_kind=row["system_kind"],
            external_id=row["external_id"],
            object_type=row["object_type"],
            canonical_uri=row["canonical_uri"],
            acl_scopes=list(row["object_acl_scopes"] or []),
            classification=row["object_data_classification"],
            acl_snapshot=AclSnapshot(
                id=row["acl_snapshot_id"],
                version=row["acl_snapshot_version"],
                provider=row["acl_snapshot_provider"],
                scopes=list(row["acl_snapshot_scopes"] or []),
                captured_at=row["acl_snapshot_captured_at"],
                expires_at=row["acl_snapshot_expires_at"],
                configuration_owned=row["acl_snapshot_configuration_owned"],
            ),
            metadata=row["object_metadata"] or {},
        )
        revision = SourceRevision(
            id=row["revision_id"],
            object_id=row["object_id"],
            revision_key=row["revision_key"],
            sha256=row["revision_sha256"],
            size_bytes=row["revision_size_bytes"],
            source_modified_at=row["source_modified_at"],
            raw_object_uri=row["raw_object_uri"],
            is_tombstone=row["is_tombstone"],
            metadata=row["revision_metadata"] or {},
        )
        return ArtifactView(
            artifact=artifact, document=document, source_object=source_object, revision=revision
        )

    def get_document(self, context: RequestContext, document_id: str) -> dict[str, Any]:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM content.logical_documents WHERE workspace_id=%s AND id=%s",
                (context.workspace, document_id),
            )
            document = cursor.fetchone()
            if not document:
                raise NotFoundError(f"document not found: {document_id}")
            cursor.execute(
                """
                SELECT a.id, a.file_name, a.extension, a.media_type, a.sha256, a.source_path,
                       da.representation_role, da.is_primary
                FROM content.document_artifacts da
                JOIN content.artifacts a ON a.id=da.artifact_id
                JOIN source.revisions r ON r.id=a.revision_id
                JOIN source.objects o ON o.id=r.object_id AND o.current_revision_id=r.id
                WHERE da.workspace_id=%s AND da.document_id=%s
                  AND (cardinality(o.acl_scopes)=0 OR o.acl_scopes <@ %s::text[])
                  AND kip.acl_snapshot_is_fresh(o.acl_snapshot_id)
                ORDER BY da.is_primary DESC, a.file_name
                """,
                (context.workspace, document_id, context.acl_scopes),
            )
            artifacts = cursor.fetchall()
            if not artifacts:
                raise NotFoundError(f"document not found: {document_id}")
        return {"document": dict(document), "artifacts": [dict(row) for row in artifacts]}

    def graph_neighbors(
        self,
        context: RequestContext,
        request: GraphNeighborsRequest,
        *,
        ontology_version: str | None = None,
    ) -> list[GraphEdge]:
        conditions = [
            "a.workspace_id=%s",
            "(a.valid_from IS NULL OR a.valid_from <= statement_timestamp())",
            "(a.valid_to IS NULL OR a.valid_to > statement_timestamp())",
            "(cardinality(a.acl_scopes)=0 OR a.acl_scopes <@ %s::text[])",
            "NOT EXISTS (SELECT 1 FROM unnest(a.evidence_acl_snapshot_ids) snapshot_id "
            "WHERE NOT kip.acl_snapshot_is_fresh(snapshot_id))",
        ]
        params: list[Any] = [context.workspace, context.acl_scopes]
        if ontology_version is not None:
            conditions.append("a.ontology_version=%s")
            params.append(ontology_version)
        if request.approved_only:
            conditions.append("a.status='active'")
        if request.predicates:
            conditions.append("a.predicate = ANY(%s::text[])")
            params.append(request.predicates)
        if request.direction == "out":
            conditions.append("a.subject_id=%s")
            params.append(request.node_id)
        elif request.direction == "in":
            conditions.append("a.object_entity_id=%s")
            params.append(request.node_id)
        else:
            conditions.append("(a.subject_id=%s OR a.object_entity_id=%s)")
            params.extend([request.node_id, request.node_id])
        params.append(request.limit)
        sql = f"""
            SELECT a.*, coalesce(array_agg(e.content_unit_id) FILTER (WHERE e.content_unit_id IS NOT NULL), ARRAY[]::text[]) AS evidence_ids
            FROM knowledge.assertions a
            LEFT JOIN knowledge.assertion_evidence e ON e.assertion_id=a.id
            WHERE {" AND ".join(conditions)}
            GROUP BY a.id
            ORDER BY a.created_at DESC
            LIMIT %s
        """
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [self._graph_edge(row) for row in rows]

    def graph_path(
        self,
        context: RequestContext,
        request: GraphPathRequest,
        *,
        ontology_version: str | None = None,
    ) -> list[GraphPath]:
        ontology_clause = ""
        ontology_params: list[Any] = []
        if ontology_version is not None:
            ontology_clause = "AND a.ontology_version=%s"
            ontology_params.append(ontology_version)
        predicate_clause = ""
        predicate_params: list[Any] = []
        if request.predicates:
            predicate_clause = "AND a.predicate = ANY(%s::text[])"
            predicate_params.append(request.predicates)
        status_clause = "AND a.status='active'" if request.approved_only else ""
        sql = f"""
            WITH RECURSIVE walk(current_node, node_ids, assertion_ids, predicates, depth) AS (
                SELECT %s::text, ARRAY[%s::text], ARRAY[]::text[], ARRAY[]::text[], 0
                UNION ALL
                SELECT
                    CASE WHEN a.subject_id=w.current_node THEN a.object_entity_id ELSE a.subject_id END,
                    w.node_ids || CASE WHEN a.subject_id=w.current_node THEN a.object_entity_id ELSE a.subject_id END,
                    w.assertion_ids || a.id,
                    w.predicates || a.predicate,
                    w.depth + 1
                FROM walk w
                JOIN knowledge.assertions a
                  ON (a.subject_id=w.current_node OR a.object_entity_id=w.current_node)
                WHERE a.workspace_id=%s
                  {status_clause}
                  {ontology_clause}
                  {predicate_clause}
                  AND (a.valid_from IS NULL OR a.valid_from <= statement_timestamp())
                  AND (a.valid_to IS NULL OR a.valid_to > statement_timestamp())
                  AND a.object_entity_id IS NOT NULL
                  AND (cardinality(a.acl_scopes)=0 OR a.acl_scopes <@ %s::text[])
                  AND NOT EXISTS (
                      SELECT 1
                      FROM unnest(a.evidence_acl_snapshot_ids) snapshot_id
                      WHERE NOT kip.acl_snapshot_is_fresh(snapshot_id)
                  )
                  AND w.depth < %s
                  AND NOT (CASE WHEN a.subject_id=w.current_node THEN a.object_entity_id ELSE a.subject_id END = ANY(w.node_ids))
            )
            SELECT node_ids, assertion_ids, predicates, depth
            FROM walk
            WHERE current_node=%s AND depth>0
            ORDER BY depth
            LIMIT {GRAPH_PATH_RESULT_CAP}
        """
        params: list[Any] = [request.from_node_id, request.from_node_id, context.workspace]
        params.extend(ontology_params)
        params.extend(predicate_params)
        params.extend([context.acl_scopes, request.max_depth, request.to_node_id])
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [
            GraphPath(
                node_ids=list(row["node_ids"]),
                assertion_ids=list(row["assertion_ids"]),
                predicates=list(row["predicates"]),
                depth=row["depth"],
            )
            for row in rows
        ]

    @staticmethod
    def _graph_edge(row: dict[str, Any]) -> GraphEdge:
        return GraphEdge(
            assertion_id=row["id"],
            subject_id=row["subject_id"],
            predicate=row["predicate"],
            object_entity_id=row["object_entity_id"],
            object_value=row["object_value"],
            status=row["status"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            ontology_version=row["ontology_version"],
            evidence_unit_ids=list(row["evidence_ids"] or []),
        )

    def enqueue_job(
        self,
        context: RequestContext,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> str:
        public_id = new_id("job")
        with self._connection(context) as connection:
            self._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO jobs.queue(public_id, workspace_id, job_type, payload, idempotency_key)
                    VALUES (%s,%s,%s,%s::jsonb,%s)
                    ON CONFLICT (workspace_id, idempotency_key) DO UPDATE SET
                        payload=EXCLUDED.payload,
                        status=CASE
                            WHEN jobs.queue.status IN ('succeeded','failed') THEN 'queued'
                            ELSE jobs.queue.status
                        END,
                        attempts=CASE
                            WHEN jobs.queue.status IN ('succeeded','failed') THEN 0
                            ELSE jobs.queue.attempts
                        END,
                        available_at=CASE
                            WHEN jobs.queue.status IN ('succeeded','failed') THEN now()
                            ELSE jobs.queue.available_at
                        END,
                        last_error=CASE
                            WHEN jobs.queue.status IN ('succeeded','failed') THEN NULL
                            ELSE jobs.queue.last_error
                        END,
                        updated_at=now()
                    RETURNING public_id
                    """,
                    (public_id, context.workspace, job_type, _json(payload), idempotency_key),
                )
                row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise DependencyUnavailableError("PostgreSQL did not return the enqueued job")
        return str(row["public_id"])

    def claim_job(self, context: RequestContext, worker_id: str) -> JobRecord | None:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT id FROM jobs.queue
                    WHERE workspace_id=%s AND status='queued' AND available_at<=now()
                    ORDER BY priority, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE jobs.queue q
                SET status='running', locked_at=now(), locked_by=%s, attempts=attempts+1, updated_at=now()
                FROM candidate c
                WHERE q.id=c.id
                RETURNING q.public_id, q.job_type, q.payload, q.status, q.attempts, q.max_attempts
                """,
                (context.workspace, worker_id),
            )
            row = cursor.fetchone()
            connection.commit()
        if not row:
            return None
        return JobRecord(
            id=row["public_id"],
            job_type=row["job_type"],
            payload=row["payload"],
            status=row["status"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
        )

    def complete_job(self, context: RequestContext, job_id: str) -> None:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE jobs.queue SET status='succeeded', updated_at=now() WHERE workspace_id=%s AND public_id=%s",
                (context.workspace, job_id),
            )
            connection.commit()

    def fail_job(self, context: RequestContext, job_id: str, error: str) -> None:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs.queue SET
                    status=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END,
                    last_error=%s,
                    available_at=CASE WHEN attempts>=max_attempts THEN available_at ELSE now() + make_interval(secs => least(300, power(2, attempts)::int)) END,
                    locked_at=NULL, locked_by=NULL, updated_at=now()
                WHERE workspace_id=%s AND public_id=%s
                """,
                (error[:8000], context.workspace, job_id),
            )
            connection.commit()

    def list_jobs(
        self, context: RequestContext, status: str | None = None, limit: int = 100
    ) -> list[JobRecord]:
        with self._connection(context) as connection, connection.cursor() as cursor:
            if status:
                cursor.execute(
                    "SELECT public_id,job_type,payload,status,attempts,max_attempts,last_error FROM jobs.queue WHERE workspace_id=%s AND status=%s ORDER BY id DESC LIMIT %s",
                    (context.workspace, status, limit),
                )
            else:
                cursor.execute(
                    "SELECT public_id,job_type,payload,status,attempts,max_attempts,last_error FROM jobs.queue WHERE workspace_id=%s ORDER BY id DESC LIMIT %s",
                    (context.workspace, limit),
                )
            rows = cursor.fetchall()
        return [
            JobRecord(
                id=row["public_id"],
                job_type=row["job_type"],
                payload=row["payload"],
                status=row["status"],
                attempts=row["attempts"],
                max_attempts=row["max_attempts"],
                last_error=row["last_error"],
            )
            for row in rows
        ]

    def record_job_result(
        self,
        context: RequestContext,
        job_id: str,
        result: dict[str, Any],
    ) -> None:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE jobs.queue
                SET payload = payload || jsonb_build_object('result', %s::jsonb),
                    updated_at = now()
                WHERE workspace_id=%s AND public_id=%s
                """,
                (_json(result), context.workspace, job_id),
            )
            updated = cursor.rowcount
            connection.commit()
        if not updated:
            raise NotFoundError(f"job not found: {job_id}")

    def save_entity(
        self,
        context: RequestContext,
        entity: KnowledgeEntity,
    ) -> KnowledgeEntity:
        if not set(entity.acl_scopes).issubset(context.acl_scopes):
            raise AuthorizationError("entity ACL scopes exceed the creator's access")
        normalized_names = [
            entity.canonical_name_normalized,
            *(normalize_entity_name(alias) for alias in entity.aliases),
        ]
        with self._connection(context) as connection:
            self._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM knowledge.entities WHERE workspace_id=%s AND id=%s FOR UPDATE",
                    (context.workspace, entity.id),
                )
                existing_row = cursor.fetchone()
                if existing_row is not None:
                    existing = self._entity(existing_row)
                    if existing != entity:
                        raise ConflictError(f"entity already exists: {entity.id}")
                    return existing
                cursor.execute(
                    """
                    SELECT entity_id, alias_normalized
                    FROM knowledge.entity_aliases
                    WHERE workspace_id=%s AND alias_normalized = ANY(%s::text[])
                    """,
                    (context.workspace, normalized_names),
                )
                collision = cursor.fetchone()
                if collision is not None:
                    raise ConflictError(
                        "entity name or alias already exists: " + str(collision["alias_normalized"])
                    )
                cursor.execute(
                    """
                    INSERT INTO knowledge.entities(
                        id, workspace_id, entity_type, canonical_name,
                        canonical_name_normalized, aliases, status, acl_scopes, metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    """,
                    (
                        entity.id,
                        context.workspace,
                        entity.entity_type,
                        entity.canonical_name,
                        entity.canonical_name_normalized,
                        entity.aliases,
                        entity.status,
                        entity.acl_scopes,
                        _json(entity.metadata),
                    ),
                )
                for display, normalized in zip(
                    [entity.canonical_name, *entity.aliases],
                    normalized_names,
                    strict=True,
                ):
                    cursor.execute(
                        """
                        INSERT INTO knowledge.entity_aliases(
                            id, workspace_id, entity_id, alias_display, alias_normalized
                        ) VALUES (%s,%s,%s,%s,%s)
                        """,
                        (
                            stable_id(
                                "alias",
                                f"{context.workspace}:{entity.id}",
                                normalized,
                            ),
                            context.workspace,
                            entity.id,
                            display,
                            normalized,
                        ),
                    )
            connection.commit()
        return entity

    def get_entity(
        self,
        context: RequestContext,
        entity_id: str,
    ) -> KnowledgeEntity:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM knowledge.entities
                WHERE workspace_id=%s AND id=%s
                  AND (
                      cardinality(acl_scopes)=0
                      OR acl_scopes <@ %s::text[]
                  )
                """,
                (context.workspace, entity_id, context.acl_scopes),
            )
            row = cursor.fetchone()
        if row is None:
            raise NotFoundError(f"entity not found: {entity_id}")
        return self._entity(row)

    def list_entities(
        self,
        context: RequestContext,
        *,
        limit: int = 100,
    ) -> list[KnowledgeEntity]:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM knowledge.entities
                WHERE workspace_id=%s AND status='active'
                  AND (
                      cardinality(acl_scopes)=0
                      OR acl_scopes <@ %s::text[]
                  )
                ORDER BY canonical_name_normalized, id
                LIMIT %s
                """,
                (context.workspace, context.acl_scopes, limit),
            )
            rows = cursor.fetchall()
        return [self._entity(row) for row in rows]

    def resolve_entities(
        self,
        context: RequestContext,
        normalized_text: str,
        *,
        limit: int = 20,
    ) -> list[KnowledgeEntity]:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT entity.*,
                       min(strpos(%s, alias.alias_normalized)) AS match_position,
                       max(length(alias.alias_normalized)) AS match_length
                FROM knowledge.entities entity
                JOIN knowledge.entity_aliases alias
                  ON alias.workspace_id=entity.workspace_id
                 AND alias.entity_id=entity.id
                WHERE entity.workspace_id=%s
                  AND entity.status='active'
                  AND strpos(%s, alias.alias_normalized) > 0
                  AND (
                      cardinality(entity.acl_scopes)=0
                      OR entity.acl_scopes <@ %s::text[]
                  )
                GROUP BY entity.id
                ORDER BY match_position, match_length DESC, entity.id
                LIMIT %s
                """,
                (
                    normalized_text,
                    context.workspace,
                    normalized_text,
                    context.acl_scopes,
                    limit,
                ),
            )
            rows = cursor.fetchall()
        return [self._entity(row) for row in rows]

    def save_entity_candidate(
        self,
        context: RequestContext,
        candidate: EntityCandidate,
    ) -> EntityCandidate:
        evidence_ids = [item.content_unit_id for item in candidate.evidence]
        with self._connection(context) as connection:
            self._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM content.units
                    WHERE workspace_id=%s
                      AND id = ANY(%s::text[])
                      AND (cardinality(acl_scopes)=0 OR acl_scopes <@ %s::text[])
                      AND kip.acl_snapshot_is_fresh(acl_snapshot_id)
                    """,
                    (context.workspace, evidence_ids, context.acl_scopes),
                )
                if len(cursor.fetchall()) != len(evidence_ids):
                    raise NotFoundError(
                        "one or more entity candidate evidence units are unavailable"
                    )
                cursor.execute(
                    """
                    INSERT INTO knowledge.entity_candidates(
                        id, workspace_id, fingerprint, entity_type, canonical_name,
                        aliases, status, origin, confidence, ontology_version,
                        evidence, derivation, review_note
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s
                    )
                    ON CONFLICT (workspace_id, fingerprint) DO NOTHING
                    """,
                    (
                        candidate.id,
                        context.workspace,
                        candidate.fingerprint,
                        candidate.entity_type,
                        candidate.canonical_name,
                        candidate.aliases,
                        candidate.status,
                        candidate.origin,
                        candidate.confidence,
                        candidate.ontology_version,
                        _json(candidate.evidence),
                        _json(candidate.derivation),
                        candidate.review_note,
                    ),
                )
                cursor.execute(
                    """
                    SELECT * FROM knowledge.entity_candidates
                    WHERE workspace_id=%s AND fingerprint=%s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(
                              knowledge.entity_candidates.evidence
                          ) item
                          WHERE NOT EXISTS (
                              SELECT 1 FROM content.units unit
                              WHERE unit.workspace_id=%s
                                AND unit.id=item->>'content_unit_id'
                                AND (
                                    cardinality(unit.acl_scopes)=0
                                    OR unit.acl_scopes <@ %s::text[]
                                )
                                AND kip.acl_snapshot_is_fresh(unit.acl_snapshot_id)
                          )
                      )
                    """,
                    (
                        context.workspace,
                        candidate.fingerprint,
                        context.workspace,
                        context.acl_scopes,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise NotFoundError("entity candidate is unavailable")
                stored = self._entity_candidate(row)
                for evidence in stored.evidence:
                    cursor.execute(
                        """
                        INSERT INTO knowledge.entity_candidate_evidence(
                            workspace_id, candidate_id, content_unit_id,
                            source_revision_sha256, locator, quote_hash
                        ) VALUES (%s,%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            context.workspace,
                            stored.id,
                            evidence.content_unit_id,
                            evidence.source_revision_sha256,
                            _json(evidence.locator),
                            evidence.quote_hash,
                        ),
                    )
            connection.commit()
        return stored

    def get_entity_candidate_by_fingerprint(
        self,
        context: RequestContext,
        fingerprint: str,
    ) -> EntityCandidate | None:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM knowledge.entity_candidates
                WHERE workspace_id=%s AND fingerprint=%s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          knowledge.entity_candidates.evidence
                      ) item
                      WHERE NOT EXISTS (
                          SELECT 1 FROM content.units unit
                          WHERE unit.workspace_id=%s
                            AND unit.id=item->>'content_unit_id'
                            AND (
                                cardinality(unit.acl_scopes)=0
                                OR unit.acl_scopes <@ %s::text[]
                            )
                            AND kip.acl_snapshot_is_fresh(unit.acl_snapshot_id)
                      )
                  )
                """,
                (
                    context.workspace,
                    fingerprint,
                    context.workspace,
                    context.acl_scopes,
                ),
            )
            row = cursor.fetchone()
        return self._entity_candidate(row) if row is not None else None

    def get_entity_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
    ) -> EntityCandidate:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM knowledge.entity_candidates
                WHERE workspace_id=%s AND id=%s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          knowledge.entity_candidates.evidence
                      ) item
                      WHERE NOT EXISTS (
                          SELECT 1 FROM content.units unit
                          WHERE unit.workspace_id=%s
                            AND unit.id=item->>'content_unit_id'
                            AND (
                                cardinality(unit.acl_scopes)=0
                                OR unit.acl_scopes <@ %s::text[]
                            )
                            AND kip.acl_snapshot_is_fresh(unit.acl_snapshot_id)
                      )
                  )
                """,
                (
                    context.workspace,
                    candidate_id,
                    context.workspace,
                    context.acl_scopes,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise NotFoundError(f"entity candidate not found: {candidate_id}")
        return self._entity_candidate(row)

    def list_entity_candidates(
        self,
        context: RequestContext,
        status: str = "proposed",
        limit: int = 100,
    ) -> list[EntityCandidate]:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM knowledge.entity_candidates
                WHERE workspace_id=%s AND status=%s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          knowledge.entity_candidates.evidence
                      ) item
                      WHERE NOT EXISTS (
                          SELECT 1 FROM content.units unit
                          WHERE unit.workspace_id=%s
                            AND unit.id=item->>'content_unit_id'
                            AND (
                                cardinality(unit.acl_scopes)=0
                                OR unit.acl_scopes <@ %s::text[]
                            )
                            AND kip.acl_snapshot_is_fresh(unit.acl_snapshot_id)
                      )
                  )
                ORDER BY created_at, id
                LIMIT %s
                """,
                (
                    context.workspace,
                    status,
                    context.workspace,
                    context.acl_scopes,
                    limit,
                ),
            )
            rows = cursor.fetchall()
        return [self._entity_candidate(row) for row in rows]

    def approve_entity_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> KnowledgeEntity:
        with self._connection(context) as connection:
            self._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM knowledge.entity_candidates
                    WHERE workspace_id=%s AND id=%s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(
                              knowledge.entity_candidates.evidence
                          ) item
                          WHERE NOT EXISTS (
                              SELECT 1 FROM content.units unit
                              WHERE unit.workspace_id=%s
                                AND unit.id=item->>'content_unit_id'
                                AND (
                                    cardinality(unit.acl_scopes)=0
                                    OR unit.acl_scopes <@ %s::text[]
                                )
                                AND kip.acl_snapshot_is_fresh(unit.acl_snapshot_id)
                          )
                      )
                    FOR UPDATE
                    """,
                    (
                        context.workspace,
                        candidate_id,
                        context.workspace,
                        context.acl_scopes,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise NotFoundError(f"entity candidate not found: {candidate_id}")
                candidate = self._entity_candidate(row)
                if candidate.status != "proposed":
                    raise ConflictError(f"entity candidate is already {candidate.status}")
                evidence_ids = [evidence.content_unit_id for evidence in candidate.evidence]
                cursor.execute(
                    """
                    SELECT id, acl_scopes
                    FROM content.units
                    WHERE workspace_id=%s
                      AND id = ANY(%s::text[])
                      AND (cardinality(acl_scopes)=0 OR acl_scopes <@ %s::text[])
                      AND kip.acl_snapshot_is_fresh(acl_snapshot_id)
                    """,
                    (context.workspace, evidence_ids, context.acl_scopes),
                )
                evidence_rows = cursor.fetchall()
                if len(evidence_rows) != len(evidence_ids):
                    raise NotFoundError(
                        "one or more entity candidate evidence units are unavailable"
                    )
                scopes = sorted(
                    {
                        str(scope)
                        for evidence_row in evidence_rows
                        for scope in evidence_row["acl_scopes"] or []
                    }
                ) or list(context.acl_scopes)
                if not set(scopes).issubset(context.acl_scopes):
                    raise AuthorizationError("reviewer lacks one or more evidence scopes")
                entity = KnowledgeEntity(
                    id=stable_entity_id(candidate.fingerprint),
                    entity_type=candidate.entity_type,
                    canonical_name=candidate.canonical_name,
                    aliases=candidate.aliases,
                    acl_scopes=scopes,
                    metadata={
                        "source_candidate_id": candidate.id,
                        "approved_by": reviewer_id,
                    },
                )
                names = [
                    entity.canonical_name_normalized,
                    *(normalize_entity_name(alias) for alias in entity.aliases),
                ]
                cursor.execute(
                    """
                    SELECT alias_normalized
                    FROM knowledge.entity_aliases
                    WHERE workspace_id=%s AND alias_normalized = ANY(%s::text[])
                    """,
                    (context.workspace, names),
                )
                collision = cursor.fetchone()
                if collision is not None:
                    raise ConflictError(
                        "entity name or alias already exists: " + str(collision["alias_normalized"])
                    )
                cursor.execute(
                    """
                    INSERT INTO knowledge.entities(
                        id, workspace_id, entity_type, canonical_name,
                        canonical_name_normalized, aliases, status, acl_scopes, metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,'active',%s,%s::jsonb)
                    """,
                    (
                        entity.id,
                        context.workspace,
                        entity.entity_type,
                        entity.canonical_name,
                        entity.canonical_name_normalized,
                        entity.aliases,
                        entity.acl_scopes,
                        _json(entity.metadata),
                    ),
                )
                for display, normalized in zip(
                    [entity.canonical_name, *entity.aliases],
                    names,
                    strict=True,
                ):
                    cursor.execute(
                        """
                        INSERT INTO knowledge.entity_aliases(
                            id, workspace_id, entity_id,
                            alias_display, alias_normalized
                        ) VALUES (%s,%s,%s,%s,%s)
                        """,
                        (
                            stable_id(
                                "alias",
                                f"{context.workspace}:{entity.id}",
                                normalized,
                            ),
                            context.workspace,
                            entity.id,
                            display,
                            normalized,
                        ),
                    )
                cursor.execute(
                    """
                    UPDATE knowledge.entity_candidates
                    SET status='approved', reviewed_at=now(), reviewed_by=%s,
                        review_note=%s
                    WHERE workspace_id=%s AND id=%s
                    """,
                    (reviewer_id, note, context.workspace, candidate.id),
                )
            connection.commit()
        return entity

    def reject_entity_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> EntityCandidate:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE knowledge.entity_candidates
                SET status='rejected', reviewed_at=now(), reviewed_by=%s,
                    review_note=%s
                WHERE workspace_id=%s AND id=%s AND status='proposed'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          knowledge.entity_candidates.evidence
                      ) item
                      WHERE NOT EXISTS (
                          SELECT 1 FROM content.units unit
                          WHERE unit.workspace_id=%s
                            AND unit.id=item->>'content_unit_id'
                            AND (
                                cardinality(unit.acl_scopes)=0
                                OR unit.acl_scopes <@ %s::text[]
                            )
                            AND kip.acl_snapshot_is_fresh(unit.acl_snapshot_id)
                      )
                  )
                RETURNING *
                """,
                (
                    reviewer_id,
                    note,
                    context.workspace,
                    candidate_id,
                    context.workspace,
                    context.acl_scopes,
                ),
            )
            row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise NotFoundError(f"proposed entity candidate not found: {candidate_id}")
        return self._entity_candidate(row)

    @staticmethod
    def _entity_candidate(row: dict[str, Any]) -> EntityCandidate:
        return EntityCandidate(
            id=row["id"],
            fingerprint=row["fingerprint"],
            entity_type=row["entity_type"],
            canonical_name=row["canonical_name"],
            aliases=list(row["aliases"] or []),
            status=row["status"],
            origin=row["origin"],
            confidence=row["confidence"],
            ontology_version=row["ontology_version"],
            evidence=row["evidence"],
            derivation=RelationDerivation.model_validate(row["derivation"]),
            review_note=row.get("review_note"),
        )

    @staticmethod
    def _entity(row: dict[str, Any]) -> KnowledgeEntity:
        return KnowledgeEntity(
            id=row["id"],
            entity_type=row["entity_type"],
            canonical_name=row["canonical_name"],
            canonical_name_normalized=row.get("canonical_name_normalized") or "",
            aliases=list(row.get("aliases") or []),
            status=row["status"],
            acl_scopes=list(row.get("acl_scopes") or []),
            metadata=row.get("metadata") or {},
        )

    def get_candidate_by_fingerprint(
        self,
        context: RequestContext,
        fingerprint: str,
    ) -> AssertionCandidate | None:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM knowledge.assertion_candidates
                WHERE workspace_id=%s AND fingerprint=%s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          knowledge.assertion_candidates.evidence
                      ) item
                      WHERE NOT EXISTS (
                          SELECT 1 FROM content.units unit
                          WHERE unit.workspace_id=%s
                            AND unit.id=item->>'content_unit_id'
                            AND (
                                cardinality(unit.acl_scopes)=0
                                OR unit.acl_scopes <@ %s::text[]
                            )
                            AND kip.acl_snapshot_is_fresh(unit.acl_snapshot_id)
                      )
                  )
                """,
                (
                    context.workspace,
                    fingerprint,
                    context.workspace,
                    context.acl_scopes,
                ),
            )
            row = cursor.fetchone()
        return self._candidate(row) if row is not None else None

    def find_assertions(
        self,
        context: RequestContext,
        *,
        subject_id: str,
        predicate: str,
    ) -> list[ApprovedAssertion]:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    assertion.*,
                    coalesce(
                        array_agg(evidence.content_unit_id ORDER BY evidence.content_unit_id)
                            FILTER (WHERE evidence.content_unit_id IS NOT NULL),
                        ARRAY[]::text[]
                    ) AS evidence_unit_ids
                FROM knowledge.assertions assertion
                LEFT JOIN knowledge.assertion_evidence evidence
                  ON evidence.workspace_id=assertion.workspace_id
                 AND evidence.assertion_id=assertion.id
                WHERE assertion.workspace_id=%s
                  AND assertion.subject_id=%s
                  AND assertion.predicate=%s
                  AND assertion.status='active'
                  AND (cardinality(assertion.acl_scopes)=0 OR assertion.acl_scopes <@ %s::text[])
                  AND NOT EXISTS (
                      SELECT 1
                      FROM unnest(assertion.evidence_acl_snapshot_ids) snapshot_id
                      WHERE NOT kip.acl_snapshot_is_fresh(snapshot_id)
                  )
                GROUP BY assertion.id
                ORDER BY assertion.created_at, assertion.id
                """,
                (context.workspace, subject_id, predicate, context.acl_scopes),
            )
            rows = cursor.fetchall()
        return [self._approved_assertion(row) for row in rows]

    def list_assertions(
        self,
        context: RequestContext,
        *,
        ontology_version: str,
        predicates: tuple[str, ...],
        limit: int = 10_000,
    ) -> list[ApprovedAssertion]:
        if not predicates:
            return []
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    assertion.*,
                    coalesce(
                        array_agg(evidence.content_unit_id ORDER BY evidence.content_unit_id)
                            FILTER (WHERE evidence.content_unit_id IS NOT NULL),
                        ARRAY[]::text[]
                    ) AS evidence_unit_ids
                FROM knowledge.assertions assertion
                LEFT JOIN knowledge.assertion_evidence evidence
                  ON evidence.workspace_id=assertion.workspace_id
                 AND evidence.assertion_id=assertion.id
                WHERE assertion.workspace_id=%s
                  AND assertion.ontology_version=%s
                  AND assertion.predicate = ANY(%s::text[])
                  AND assertion.status='active'
                  AND (
                      cardinality(assertion.acl_scopes)=0
                      OR assertion.acl_scopes <@ %s::text[]
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM unnest(assertion.evidence_acl_snapshot_ids) snapshot_id
                      WHERE NOT kip.acl_snapshot_is_fresh(snapshot_id)
                  )
                GROUP BY assertion.id
                ORDER BY assertion.id
                LIMIT %s
                """,
                (
                    context.workspace,
                    ontology_version,
                    list(predicates),
                    context.acl_scopes,
                    limit,
                ),
            )
            rows = cursor.fetchall()
        return [self._approved_assertion(row) for row in rows]

    def save_candidate(
        self, context: RequestContext, candidate: AssertionCandidate
    ) -> AssertionCandidate:
        fingerprint = candidate.fingerprint
        if fingerprint is None:
            payload = candidate.model_dump(
                mode="json",
                exclude={"fingerprint"},
            )
            fingerprint = "legacy:sha256:" + hashlib.sha256(_json(payload).encode()).hexdigest()
            candidate = candidate.model_copy(update={"fingerprint": fingerprint})
        with self._connection(context) as connection:
            self._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                evidence_ids = [evidence.content_unit_id for evidence in candidate.evidence]
                if evidence_ids:
                    cursor.execute(
                        """
                        SELECT id FROM content.units
                        WHERE workspace_id=%s
                          AND id = ANY(%s::text[])
                          AND (
                              cardinality(acl_scopes)=0
                              OR acl_scopes <@ %s::text[]
                          )
                          AND kip.acl_snapshot_is_fresh(acl_snapshot_id)
                        """,
                        (context.workspace, evidence_ids, context.acl_scopes),
                    )
                    if len(cursor.fetchall()) != len(set(evidence_ids)):
                        raise NotFoundError("one or more candidate evidence units are unavailable")
                cursor.execute(
                    """
                    INSERT INTO knowledge.assertion_candidates(
                        id,workspace_id,subject_id,predicate,object_entity_id,object_value,
                        status,origin,confidence,ontology_version,evidence,review_note,
                        fingerprint,valid_from,valid_to,derivation,review_risk,
                        contradicts_assertion_ids,migrates_assertion_ids
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s,
                        %s,%s,%s,%s::jsonb,%s,%s,%s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        status=EXCLUDED.status,
                        confidence=EXCLUDED.confidence,
                        evidence=EXCLUDED.evidence,
                        review_note=EXCLUDED.review_note,
                        fingerprint=EXCLUDED.fingerprint,
                        valid_from=EXCLUDED.valid_from,
                        valid_to=EXCLUDED.valid_to,
                        derivation=EXCLUDED.derivation,
                        review_risk=EXCLUDED.review_risk,
                        contradicts_assertion_ids=EXCLUDED.contradicts_assertion_ids,
                        migrates_assertion_ids=EXCLUDED.migrates_assertion_ids
                    """,
                    (
                        candidate.id,
                        context.workspace,
                        candidate.subject_id,
                        candidate.predicate,
                        candidate.object_entity_id,
                        _json(candidate.object_value)
                        if candidate.object_value is not None
                        else None,
                        candidate.status,
                        candidate.origin,
                        candidate.confidence,
                        candidate.ontology_version,
                        _json(candidate.evidence),
                        candidate.review_note,
                        candidate.fingerprint,
                        candidate.valid_from,
                        candidate.valid_to,
                        _json(candidate.derivation) if candidate.derivation is not None else None,
                        candidate.review_risk,
                        candidate.contradicts_assertion_ids,
                        candidate.migrates_assertion_ids,
                    ),
                )
                for evidence in candidate.evidence:
                    source_revision_sha256 = evidence.source_revision_sha256
                    locator = evidence.locator
                    if source_revision_sha256 is None or not locator:
                        cursor.execute(
                            """
                            SELECT revision.sha256, unit.locator
                            FROM content.units unit
                            JOIN content.artifacts artifact
                              ON artifact.workspace_id=unit.workspace_id
                             AND artifact.id=unit.artifact_id
                            JOIN source.revisions revision
                              ON revision.workspace_id=artifact.workspace_id
                             AND revision.id=artifact.revision_id
                            WHERE unit.workspace_id=%s AND unit.id=%s
                            """,
                            (context.workspace, evidence.content_unit_id),
                        )
                        evidence_row = cursor.fetchone()
                        if evidence_row is None:
                            raise NotFoundError(
                                "one or more candidate evidence units are unavailable"
                            )
                        source_revision_sha256 = source_revision_sha256 or evidence_row["sha256"]
                        locator = locator or evidence_row["locator"]
                    cursor.execute(
                        """
                        INSERT INTO knowledge.assertion_candidate_evidence(
                            workspace_id,candidate_id,content_unit_id,
                            source_revision_sha256,locator,quote_hash
                        ) VALUES (%s,%s,%s,%s,%s::jsonb,%s)
                        ON CONFLICT (workspace_id,candidate_id,content_unit_id)
                        DO UPDATE SET
                            source_revision_sha256=EXCLUDED.source_revision_sha256,
                            locator=EXCLUDED.locator,
                            quote_hash=EXCLUDED.quote_hash
                        """,
                        (
                            context.workspace,
                            candidate.id,
                            evidence.content_unit_id,
                            source_revision_sha256,
                            _json(locator),
                            evidence.quote_hash,
                        ),
                    )
            connection.commit()
        return candidate

    @staticmethod
    def _candidate_filter_sql(
        context: RequestContext,
        status: str,
        predicate: str | None,
        subject_id: str | None,
    ) -> tuple[str, list[Any]]:
        conditions = [
            "workspace_id=%s",
            "status=%s",
            """NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    knowledge.assertion_candidates.evidence
                ) item
                WHERE NOT EXISTS (
                    SELECT 1 FROM content.units unit
                    WHERE unit.workspace_id=%s
                      AND unit.id=item->>'content_unit_id'
                      AND (
                          cardinality(unit.acl_scopes)=0
                          OR unit.acl_scopes <@ %s::text[]
                      )
                      AND kip.acl_snapshot_is_fresh(unit.acl_snapshot_id)
                )
            )""",
        ]
        params: list[Any] = [
            context.workspace,
            status,
            context.workspace,
            context.acl_scopes,
        ]
        if predicate is not None:
            conditions.append("predicate=%s")
            params.append(predicate)
        if subject_id is not None:
            conditions.append("subject_id=%s")
            params.append(subject_id)
        return " AND ".join(conditions), params

    def list_candidates(
        self,
        context: RequestContext,
        status: str = "proposed",
        limit: int = 100,
        *,
        predicate: str | None = None,
        subject_id: str | None = None,
    ) -> list[AssertionCandidate]:
        where, params = self._candidate_filter_sql(
            context, status, predicate, subject_id
        )
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT * FROM knowledge.assertion_candidates
                WHERE {where}
                ORDER BY {_REVIEW_RISK_ORDER_SQL},
                         confidence DESC NULLS LAST,
                         created_at,
                         id
                LIMIT %s
                """,
                (*params, limit),
            )
            rows = cursor.fetchall()
        return [self._candidate(row) for row in rows]

    def count_candidates(
        self,
        context: RequestContext,
        status: str = "proposed",
        *,
        predicate: str | None = None,
        subject_id: str | None = None,
    ) -> int:
        where, params = self._candidate_filter_sql(
            context, status, predicate, subject_id
        )
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT count(*) FROM knowledge.assertion_candidates WHERE {where}",
                params,
            )
            row = cursor.fetchone()
        if row is None:
            raise DependencyUnavailableError(
                "PostgreSQL did not return the candidate count"
            )
        return int(row["count"])

    def get_candidate(self, context: RequestContext, candidate_id: str) -> AssertionCandidate:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM knowledge.assertion_candidates
                WHERE workspace_id=%s AND id=%s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          knowledge.assertion_candidates.evidence
                      ) item
                      WHERE NOT EXISTS (
                          SELECT 1 FROM content.units unit
                          WHERE unit.workspace_id=%s
                            AND unit.id=item->>'content_unit_id'
                            AND (
                                cardinality(unit.acl_scopes)=0
                                OR unit.acl_scopes <@ %s::text[]
                            )
                            AND kip.acl_snapshot_is_fresh(unit.acl_snapshot_id)
                      )
                  )
                """,
                (
                    context.workspace,
                    candidate_id,
                    context.workspace,
                    context.acl_scopes,
                ),
            )
            row = cursor.fetchone()
        if not row:
            raise NotFoundError(f"candidate not found: {candidate_id}")
        return self._candidate(row)

    @staticmethod
    def _candidate(row: dict[str, Any]) -> AssertionCandidate:
        return AssertionCandidate(
            id=row["id"],
            subject_id=row["subject_id"],
            predicate=row["predicate"],
            object_entity_id=row["object_entity_id"],
            object_value=row["object_value"],
            status=row["status"],
            origin=row["origin"],
            confidence=row["confidence"],
            ontology_version=row["ontology_version"],
            evidence=row["evidence"] or [],
            review_note=row.get("review_note"),
            fingerprint=row.get("fingerprint"),
            valid_from=row.get("valid_from"),
            valid_to=row.get("valid_to"),
            derivation=(
                RelationDerivation.model_validate(row["derivation"])
                if row.get("derivation")
                else None
            ),
            review_risk=row.get("review_risk") or "medium",
            contradicts_assertion_ids=list(row.get("contradicts_assertion_ids") or []),
            migrates_assertion_ids=list(row.get("migrates_assertion_ids") or []),
        )

    def approve_candidate(
        self,
        context: RequestContext,
        candidate_id: str,
        reviewer_id: str,
        note: str | None = None,
        *,
        supersede_assertion_ids: Sequence[str] = (),
    ) -> ApprovedAssertion:
        with self._connection(context) as connection:
            self._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM knowledge.assertion_candidates
                    WHERE workspace_id=%s AND id=%s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM jsonb_array_elements(
                              knowledge.assertion_candidates.evidence
                          ) item
                          WHERE NOT EXISTS (
                              SELECT 1 FROM content.units unit
                              WHERE unit.workspace_id=%s
                                AND unit.id=item->>'content_unit_id'
                                AND (
                                    cardinality(unit.acl_scopes)=0
                                    OR unit.acl_scopes <@ %s::text[]
                                )
                                AND kip.acl_snapshot_is_fresh(unit.acl_snapshot_id)
                          )
                      )
                    FOR UPDATE
                    """,
                    (
                        context.workspace,
                        candidate_id,
                        context.workspace,
                        context.acl_scopes,
                    ),
                )
                row = cursor.fetchone()
                if not row:
                    raise NotFoundError(f"candidate not found: {candidate_id}")
                candidate = self._candidate(row)
                if candidate.status != "proposed":
                    raise ConflictError(f"candidate is already {candidate.status}")
                if _evidence_required(candidate) and not candidate.evidence:
                    raise ValidationError(f"predicate {candidate.predicate} requires evidence")
                unknown_supersedes = sorted(
                    set(supersede_assertion_ids)
                    - set(candidate.contradicts_assertion_ids)
                )
                if unknown_supersedes:
                    raise ValidationError(
                        "supersede targets must be contradicted by the candidate: "
                        + ", ".join(unknown_supersedes)
                    )
                evidence_unit_ids = [evidence.content_unit_id for evidence in candidate.evidence]
                derived_scopes: set[str] = set()
                evidence_rows: list[dict[str, Any]] = []
                if evidence_unit_ids:
                    cursor.execute(
                        """
                        SELECT id, acl_scopes, acl_snapshot_id
                        FROM content.units
                        WHERE workspace_id=%s
                          AND id = ANY(%s::text[])
                          AND (
                              cardinality(acl_scopes)=0
                              OR acl_scopes <@ %s::text[]
                          )
                          AND kip.acl_snapshot_is_fresh(acl_snapshot_id)
                        """,
                        (
                            context.workspace,
                            evidence_unit_ids,
                            context.acl_scopes,
                        ),
                    )
                    evidence_rows = cursor.fetchall()
                    if len(evidence_rows) != len(set(evidence_unit_ids)):
                        raise NotFoundError("one or more evidence units are unavailable")
                    for evidence_row in evidence_rows:
                        derived_scopes.update(evidence_row["acl_scopes"] or [])
                assertion_scopes = sorted(derived_scopes) or list(context.acl_scopes)
                assertion_snapshot_ids = sorted(
                    {str(evidence_row["acl_snapshot_id"]) for evidence_row in evidence_rows}
                )
                if not set(assertion_scopes).issubset(set(context.acl_scopes)):
                    raise AuthorizationError("reviewer lacks one or more evidence scopes")

                assertion_id = new_id("ast")
                cursor.execute(
                    """
                    INSERT INTO knowledge.assertions(
                        id,workspace_id,subject_id,predicate,object_entity_id,object_value,status,
                        ontology_version,source_candidate_id,acl_scopes,
                        evidence_acl_snapshot_ids,created_by,valid_from,valid_to
                    ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,'active',%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        assertion_id,
                        context.workspace,
                        candidate.subject_id,
                        candidate.predicate,
                        candidate.object_entity_id,
                        _json(candidate.object_value)
                        if candidate.object_value is not None
                        else None,
                        candidate.ontology_version,
                        candidate.id,
                        assertion_scopes,
                        assertion_snapshot_ids,
                        reviewer_id,
                        candidate.valid_from,
                        candidate.valid_to,
                    ),
                )
                for evidence in candidate.evidence:
                    unit_id = evidence.content_unit_id
                    locator = evidence.locator or {
                        "type": "content_unit",
                        "data": {"unit_id": unit_id},
                    }
                    cursor.execute(
                        """
                        INSERT INTO knowledge.assertion_evidence(workspace_id, assertion_id, content_unit_id, locator, quote_hash)
                        VALUES (%s,%s,%s,%s::jsonb,%s)
                        """,
                        (
                            context.workspace,
                            assertion_id,
                            unit_id,
                            _json(locator),
                            evidence.quote_hash,
                        ),
                    )
                cursor.execute(
                    """
                    UPDATE knowledge.assertion_candidates SET status='approved', reviewed_at=now(), reviewed_by=%s, review_note=%s
                    WHERE workspace_id=%s AND id=%s
                    """,
                    (reviewer_id, note, context.workspace, candidate_id),
                )
                for superseded_id in supersede_assertion_ids:
                    cursor.execute(
                        """
                        UPDATE knowledge.assertions
                        SET status='superseded', superseded_by=%s
                        WHERE workspace_id=%s AND id=%s AND status='active'
                          AND (
                              cardinality(acl_scopes)=0
                              OR acl_scopes <@ %s::text[]
                          )
                        """,
                        (
                            assertion_id,
                            context.workspace,
                            superseded_id,
                            context.acl_scopes,
                        ),
                    )
                    if not cursor.rowcount:
                        raise ConflictError(
                            f"assertion is not active or not visible: {superseded_id}"
                        )
            connection.commit()
        return ApprovedAssertion(
            id=assertion_id,
            subject_id=candidate.subject_id,
            predicate=candidate.predicate,
            object_entity_id=candidate.object_entity_id,
            object_value=candidate.object_value,
            ontology_version=candidate.ontology_version,
            source_candidate_id=candidate.id,
            acl_scopes=assertion_scopes,
            evidence_unit_ids=evidence_unit_ids,
            evidence_acl_snapshot_ids=assertion_snapshot_ids,
            valid_from=candidate.valid_from,
            valid_to=candidate.valid_to,
        )

    def reject_candidate(
        self, context: RequestContext, candidate_id: str, reviewer_id: str, note: str | None = None
    ) -> AssertionCandidate:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE knowledge.assertion_candidates
                SET status='rejected', reviewed_at=now(), reviewed_by=%s, review_note=%s
                WHERE workspace_id=%s AND id=%s AND status='proposed'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(
                          knowledge.assertion_candidates.evidence
                      ) item
                      WHERE NOT EXISTS (
                          SELECT 1 FROM content.units unit
                          WHERE unit.workspace_id=%s
                            AND unit.id=item->>'content_unit_id'
                            AND (
                                cardinality(unit.acl_scopes)=0
                                OR unit.acl_scopes <@ %s::text[]
                            )
                            AND kip.acl_snapshot_is_fresh(unit.acl_snapshot_id)
                      )
                  )
                RETURNING *
                """,
                (
                    reviewer_id,
                    note,
                    context.workspace,
                    candidate_id,
                    context.workspace,
                    context.acl_scopes,
                ),
            )
            row = cursor.fetchone()
            connection.commit()
        if not row:
            raise NotFoundError(f"proposed candidate not found: {candidate_id}")
        return self._candidate(row)

    def get_assertion(self, context: RequestContext, assertion_id: str) -> ApprovedAssertion:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    a.*,
                    coalesce(
                        array_agg(e.content_unit_id ORDER BY e.content_unit_id)
                            FILTER (WHERE e.content_unit_id IS NOT NULL),
                        ARRAY[]::text[]
                    ) AS evidence_unit_ids
                FROM knowledge.assertions a
                LEFT JOIN knowledge.assertion_evidence e
                    ON e.workspace_id=a.workspace_id AND e.assertion_id=a.id
                WHERE a.workspace_id=%s
                  AND a.id=%s
                  AND (cardinality(a.acl_scopes)=0 OR a.acl_scopes <@ %s::text[])
                  AND NOT EXISTS (
                      SELECT 1
                      FROM unnest(a.evidence_acl_snapshot_ids) snapshot_id
                      WHERE NOT kip.acl_snapshot_is_fresh(snapshot_id)
                  )
                GROUP BY a.id
                """,
                (context.workspace, assertion_id, context.acl_scopes),
            )
            row = cursor.fetchone()
        if not row:
            raise NotFoundError(f"assertion not found: {assertion_id}")
        return self._approved_assertion(row)

    def revoke_assertion(
        self,
        context: RequestContext,
        assertion_id: str,
        reviewer_id: str,
        note: str,
    ) -> ApprovedAssertion:
        with self._connection(context) as connection:
            self._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT status FROM knowledge.assertions
                    WHERE workspace_id=%s AND id=%s
                      AND (cardinality(acl_scopes)=0 OR acl_scopes <@ %s::text[])
                    FOR UPDATE
                    """,
                    (context.workspace, assertion_id, context.acl_scopes),
                )
                row = cursor.fetchone()
                if row is None:
                    raise NotFoundError(f"assertion not found: {assertion_id}")
                if row["status"] != "active":
                    raise ConflictError(f"assertion is already {row['status']}")
                cursor.execute(
                    """
                    UPDATE knowledge.assertions
                    SET status='revoked', revoked_at=now(), revoked_by=%s,
                        revocation_note=%s
                    WHERE workspace_id=%s AND id=%s
                    """,
                    (reviewer_id, note, context.workspace, assertion_id),
                )
            connection.commit()
        return self.get_assertion(context, assertion_id)

    @staticmethod
    def _approved_assertion(row: dict[str, Any]) -> ApprovedAssertion:
        return ApprovedAssertion(
            id=row["id"],
            subject_id=row["subject_id"],
            predicate=row["predicate"],
            object_entity_id=row["object_entity_id"],
            object_value=row["object_value"],
            status=row["status"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            ontology_version=row["ontology_version"],
            source_candidate_id=row["source_candidate_id"],
            acl_scopes=row["acl_scopes"] or [],
            evidence_unit_ids=row["evidence_unit_ids"] or [],
            evidence_acl_snapshot_ids=row["evidence_acl_snapshot_ids"] or [],
            revoked_at=row.get("revoked_at"),
            revoked_by=row.get("revoked_by"),
            revocation_note=row.get("revocation_note"),
            superseded_by=row.get("superseded_by"),
        )

    def status(self, context: RequestContext) -> StatusReport:
        queries = {
            "source_objects": "SELECT count(*) FROM source.objects WHERE workspace_id=%s",
            "revisions": "SELECT count(*) FROM source.revisions WHERE workspace_id=%s",
            "artifacts": "SELECT count(*) FROM content.artifacts WHERE workspace_id=%s",
            "active_extractions": "SELECT count(*) FROM content.extractions WHERE workspace_id=%s AND active",
            "content_units": "SELECT count(*) FROM content.units WHERE workspace_id=%s",
            "lexical_units": "SELECT count(*) FROM search.lexical_units WHERE workspace_id=%s",
            "assertion_candidates": "SELECT count(*) FROM knowledge.assertion_candidates WHERE workspace_id=%s",
            "approved_assertions": "SELECT count(*) FROM knowledge.assertions WHERE workspace_id=%s AND status='active'",
            "queued_jobs": "SELECT count(*) FROM jobs.queue WHERE workspace_id=%s AND status='queued'",
            "failed_jobs": "SELECT count(*) FROM jobs.queue WHERE workspace_id=%s AND status='failed'",
        }
        values: dict[str, int] = {}
        with self._connection(context) as connection, connection.cursor() as cursor:
            for name, query in queries.items():
                cursor.execute(query, (context.workspace,))
                row = cursor.fetchone()
                if row is None:
                    raise DependencyUnavailableError(
                        f"PostgreSQL did not return status counter {name}"
                    )
                values[name] = int(row["count"])
        return StatusReport(workspace=context.workspace, repository=self.name, **values)

    def rebuild_projection(self, context: RequestContext, projection: str) -> dict[str, Any]:
        if projection not in {"lexical", "graph", "all"}:
            raise ValidationError(f"unsupported projection: {projection}")
        result: dict[str, Any] = {"projection": projection, "status": "rebuilt"}
        if projection in {"lexical", "all"}:
            with self._connection(context) as connection, connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TEMPORARY TABLE rebuilt_lexical_units ON COMMIT DROP AS
                    SELECT u.id AS unit_id,u.workspace_id,u.document_id,u.artifact_id,
                           s.kind AS source_kind,
                           coalesce(nullif(u.title,''),d.title,'') AS title,
                           u.body,u.lexical_text AS lexemes,
                           concat_ws(
                               ' ',a.file_name,d.title,
                               o.metadata->>'document_number',d.metadata->>'project_id'
                           ) AS identifier_text,
                           r.source_modified_at,r.sha256 AS source_sha256
                    FROM content.units u
                    JOIN content.extractions e ON e.id=u.extraction_id AND e.active
                    JOIN content.artifacts a ON a.id=u.artifact_id
                    JOIN source.revisions r ON r.id=a.revision_id
                    JOIN source.objects o ON o.id=r.object_id AND o.current_revision_id=r.id
                    JOIN source.systems s ON s.id=o.system_id
                    LEFT JOIN content.logical_documents d ON d.id=u.document_id
                    WHERE u.workspace_id=%s
                    """,
                    (context.workspace,),
                )
                cursor.execute(
                    """
                    DELETE FROM search.lexical_units AS current
                    WHERE current.workspace_id=%s
                      AND NOT EXISTS (
                          SELECT 1 FROM rebuilt_lexical_units AS rebuilt
                          WHERE rebuilt.unit_id=current.unit_id
                      )
                    """,
                    (context.workspace,),
                )
                deleted_units = cursor.rowcount
                cursor.execute(
                    """
                    INSERT INTO search.lexical_units AS current(
                        unit_id,workspace_id,document_id,artifact_id,source_kind,title,body,lexemes,
                        identifier_text,source_modified_at,source_sha256
                    )
                    SELECT unit_id,workspace_id,document_id,artifact_id,source_kind,title,body,lexemes,
                           identifier_text,source_modified_at,source_sha256
                    FROM rebuilt_lexical_units
                    ON CONFLICT (unit_id) DO UPDATE SET
                        workspace_id=EXCLUDED.workspace_id,
                        document_id=EXCLUDED.document_id,
                        artifact_id=EXCLUDED.artifact_id,
                        source_kind=EXCLUDED.source_kind,
                        title=EXCLUDED.title,
                        body=EXCLUDED.body,
                        lexemes=EXCLUDED.lexemes,
                        identifier_text=EXCLUDED.identifier_text,
                        source_modified_at=EXCLUDED.source_modified_at,
                        source_sha256=EXCLUDED.source_sha256,
                        updated_at=now()
                    WHERE ROW(
                        current.workspace_id,current.document_id,current.artifact_id,
                        current.source_kind,current.title,current.body,current.lexemes,
                        current.identifier_text,current.source_modified_at,current.source_sha256
                    ) IS DISTINCT FROM ROW(
                        EXCLUDED.workspace_id,EXCLUDED.document_id,EXCLUDED.artifact_id,
                        EXCLUDED.source_kind,EXCLUDED.title,EXCLUDED.body,EXCLUDED.lexemes,
                        EXCLUDED.identifier_text,EXCLUDED.source_modified_at,EXCLUDED.source_sha256
                    )
                    """,
                )
                changed_units = cursor.rowcount
                cursor.execute("SELECT count(*) FROM rebuilt_lexical_units")
                count = cursor.fetchone()
                if count is None:
                    raise DependencyUnavailableError(
                        "PostgreSQL did not return rebuilt lexical unit count"
                    )
                result["lexical_units"] = int(count["count"])
                result["changed_units"] = changed_units
                result["deleted_units"] = deleted_units
                connection.commit()
        if projection in {"graph", "all"}:
            result["graph"] = (
                "canonical assertions are queried directly; optional graph projection unchanged"
            )
        return result

    def export_canonical(self, context: RequestContext, output: Path) -> dict[str, Any]:
        output.parent.mkdir(parents=True, exist_ok=True)
        exports = [
            ("workspace", "SELECT * FROM kip.workspaces WHERE slug=%s", (context.workspace,)),
            (
                "source_system",
                "SELECT * FROM source.systems WHERE workspace_id=%s ORDER BY id",
                (context.workspace,),
            ),
            (
                "source_object",
                "SELECT * FROM source.objects WHERE workspace_id=%s ORDER BY id",
                (context.workspace,),
            ),
            (
                "source_revision",
                "SELECT * FROM source.revisions WHERE workspace_id=%s ORDER BY id",
                (context.workspace,),
            ),
            (
                "logical_document",
                "SELECT * FROM content.logical_documents WHERE workspace_id=%s ORDER BY id",
                (context.workspace,),
            ),
            (
                "artifact",
                "SELECT * FROM content.artifacts WHERE workspace_id=%s ORDER BY id",
                (context.workspace,),
            ),
            (
                "content_unit",
                "SELECT * FROM content.units WHERE workspace_id=%s ORDER BY id",
                (context.workspace,),
            ),
            (
                "assertion_candidate",
                "SELECT * FROM knowledge.assertion_candidates WHERE workspace_id=%s ORDER BY id",
                (context.workspace,),
            ),
            (
                "assertion",
                "SELECT * FROM knowledge.assertions WHERE workspace_id=%s ORDER BY id",
                (context.workspace,),
            ),
        ]
        count = 0
        with self._connection(context) as connection, output.open("w", encoding="utf-8") as handle:
            for record_type, query, params in exports:
                with connection.cursor() as cursor:
                    cursor.execute(query, params)
                    for row in cursor:
                        handle.write(_json({"type": record_type, "data": dict(row)}) + "\n")
                        count += 1
        return {
            "output": str(output),
            "records": count,
            "generated_at": datetime.now(UTC).isoformat(),
        }
