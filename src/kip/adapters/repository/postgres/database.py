from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

    from psycopg import Connection
    from psycopg.rows import DictRow

from kip.domain.models import (
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
from kip.ids import new_id

_HIGH_RISK_PREDICATES = {
    "amends",
    "supersedes",
    "approves",
    "authorizes",
    "evidences",
    "satisfies",
    "violates",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _vector_literal(values: list[float]) -> str:
    if any(not math.isfinite(value) for value in values):
        raise ValidationError("embedding contains non-finite values")
    return "[" + ",".join(format(value, ".9g") for value in values) + "]"


def _websearch_or_query(lexemes: str) -> str:
    terms = list(dict.fromkeys(term.replace('"', "") for term in lexemes.split() if term))
    return " OR ".join(f'"{term}"' for term in terms[:256])


class PostgresDatabase:
    """PostgreSQL canonical repository and baseline lexical/graph adapter."""

    name = "postgresql"

    def __init__(self, database_url: str, *, statement_timeout_ms: int = 15000) -> None:
        self.database_url = database_url
        self.statement_timeout_ms = statement_timeout_ms
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:
            raise DependencyUnavailableError("Install the postgres extra: pip install '.[postgres]'") from exc

    @contextmanager
    def _connection(
        self,
        context: RequestContext | None = None,
    ) -> Generator[Connection[DictRow], None, None]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, false)", (str(self.statement_timeout_ms),))
                if context is not None:
                    cursor.execute("SELECT set_config('kip.workspace_id', %s, true)", (context.workspace,))
                    cursor.execute(
                        "SELECT set_config('kip.acl_scopes', %s, true)",
                        (",".join(context.acl_scopes),),
                    )
            yield connection

    def migrate(self, migrations_dir: Path) -> list[str]:
        import psycopg
        from psycopg.rows import dict_row

        applied: list[str] = []
        with psycopg.connect(self.database_url, row_factory=dict_row, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, false)", (str(self.statement_timeout_ms),))
            for path in sorted(migrations_dir.glob("*.sql")):
                if path.name.startswith("9"):
                    continue
                version = path.stem
                checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                with connection.cursor() as cursor:
                    try:
                        cursor.execute("SELECT checksum FROM kip.schema_migrations WHERE version = %s", (version,))
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
                (context.principal_id, context.workspace, context.principal_id, context.principal_id),
            )

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

    def ingest_packet(self, context: RequestContext, packet: DocumentPacket) -> IngestResult:
        if packet.workspace_id != context.workspace:
            raise ValidationError("packet workspace does not match request context")

        with self._connection(context) as connection:
            self._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_revision_id FROM source.objects WHERE workspace_id=%s AND id=%s FOR UPDATE",
                    (context.workspace, packet.source_object.id),
                )
                existing_object = cursor.fetchone()
                old_revision_id = existing_object["current_revision_id"] if existing_object else None
                if old_revision_id:
                    cursor.execute(
                        "SELECT sha256 FROM source.revisions WHERE workspace_id=%s AND id=%s",
                        (context.workspace, old_revision_id),
                    )
                    old_revision = cursor.fetchone()
                    if old_revision and old_revision["sha256"] == packet.revision.sha256:
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
                        acl_scopes, metadata, last_seen_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,now())
                    ON CONFLICT (id) DO UPDATE SET
                        canonical_uri=EXCLUDED.canonical_uri,
                        acl_scopes=EXCLUDED.acl_scopes,
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
                            title, body, body_normalized, locator, acl_scopes, char_count, metadata
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb)
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
                            _json(unit.locator.model_dump(mode="json")),
                            unit.acl_scopes,
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
                                        str(packet.source_object.metadata.get("document_number", "")),
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

    def search(self, context: RequestContext, request: SearchRequest, lexemes: str) -> list[SearchHit]:
        websearch_query = _websearch_or_query(lexemes)
        conditions = ["l.workspace_id=%s", "(cardinality(u.acl_scopes)=0 OR u.acl_scopes <@ %s::text[])"]
        condition_params: list[Any] = [context.workspace, context.acl_scopes]
        if request.source_kinds:
            conditions.append("l.source_kind = ANY(%s::text[])")
            condition_params.append(request.source_kinds)
        if request.document_types:
            conditions.append("d.document_type = ANY(%s::text[])")
            condition_params.append(request.document_types)
        if request.project_ids:
            conditions.append("coalesce(d.metadata->>'project_id','') = ANY(%s::text[])")
            condition_params.append(request.project_ids)

        sql = f"""
            SELECT
                l.unit_id, l.document_id, l.artifact_id, l.source_kind, l.title,
                left(regexp_replace(l.body, '\\s+', ' ', 'g'), 500) AS snippet,
                (
                    CASE WHEN lower(l.identifier_text) = lower(%s) THEN 30 ELSE 0 END
                  + CASE WHEN lower(l.title) LIKE '%%' || lower(%s) || '%%' THEN 10 ELSE 0 END
                  + CASE WHEN lower(l.body) LIKE '%%' || lower(%s) || '%%' THEN 6 ELSE 0 END
                  + CASE WHEN l.tsv @@ websearch_to_tsquery('simple', %s) THEN ts_rank_cd(l.tsv, websearch_to_tsquery('simple', %s)) * 10 ELSE 0 END
                  + similarity(l.title, %s) * 2
                ) AS score,
                u.locator, o.canonical_uri AS source_uri, l.source_sha256,
                l.source_modified_at, a.file_name, d.document_type
            FROM search.lexical_units l
            JOIN content.units u ON u.id=l.unit_id
            JOIN content.artifacts a ON a.id=l.artifact_id
            JOIN source.revisions r ON r.id=a.revision_id
            JOIN source.objects o ON o.id=r.object_id AND o.current_revision_id=r.id
            LEFT JOIN content.logical_documents d ON d.id=l.document_id
            WHERE {' AND '.join(conditions)}
              AND (
                    lower(l.identifier_text) LIKE '%%' || lower(%s) || '%%'
                 OR lower(l.title) LIKE '%%' || lower(%s) || '%%'
                 OR lower(l.body) LIKE '%%' || lower(%s) || '%%'
                 OR l.tsv @@ websearch_to_tsquery('simple', %s)
                 OR similarity(l.title, %s) > 0.15
              )
            ORDER BY score DESC, l.unit_id
            LIMIT %s
        """
        score_params = [
            request.query,
            request.query,
            request.query,
            websearch_query,
            websearch_query,
            request.query,
        ]
        tail_params = [
            request.query,
            request.query,
            request.query,
            websearch_query,
            request.query,
            request.limit,
        ]
        all_params = score_params + condition_params + tail_params

        with self._connection(context) as connection, connection.cursor() as cursor:
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
                metadata={"file_name": row["file_name"], "document_type": row["document_type"]},
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
        if space.dimensions != 1024:
            raise ValidationError("the PostgreSQL semantic projection requires 1024 dimensions")
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
            raise DependencyUnavailableError(
                "PostgreSQL did not return the saved embedding space"
            )
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
            if dimensions != 1024:
                raise ValidationError("the PostgreSQL semantic projection requires 1024 dimensions")
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
                """
                INSERT INTO search.embeddings_1024(
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
        if len(query_embedding) != 1024:
            raise ValidationError("the PostgreSQL semantic projection requires 1024 dimensions")
        conditions = [
            "v.workspace_id=%s",
            "v.space_id=%s",
            "v.source_hash=r.sha256",
            "(cardinality(u.acl_scopes)=0 OR u.acl_scopes <@ %s::text[])",
        ]
        condition_params: list[Any] = [
            context.workspace,
            space_id,
            context.acl_scopes,
        ]
        if request.source_kinds:
            conditions.append("l.source_kind = ANY(%s::text[])")
            condition_params.append(request.source_kinds)
        if request.document_types:
            conditions.append("d.document_type = ANY(%s::text[])")
            condition_params.append(request.document_types)
        if request.project_ids:
            conditions.append("coalesce(d.metadata->>'project_id','') = ANY(%s::text[])")
            condition_params.append(request.project_ids)
        vector = _vector_literal(query_embedding)
        sql = f"""
            SELECT
                l.unit_id,l.document_id,l.artifact_id,l.source_kind,l.title,
                left(regexp_replace(l.body, '\\s+', ' ', 'g'), 500) AS snippet,
                1 - (v.embedding <=> %s::vector) AS score,
                u.locator,o.canonical_uri AS source_uri,l.source_sha256,
                l.source_modified_at,a.file_name,d.document_type
            FROM search.embeddings_1024 v
            JOIN content.units u ON u.id=v.unit_id
            JOIN search.lexical_units l ON l.unit_id=u.id
            JOIN content.artifacts a ON a.id=l.artifact_id
            JOIN source.revisions r ON r.id=a.revision_id
            JOIN source.objects o ON o.id=r.object_id AND o.current_revision_id=r.id
            LEFT JOIN content.logical_documents d ON d.id=l.document_id
            WHERE {' AND '.join(conditions)}
            ORDER BY score DESC,l.unit_id
            LIMIT %s
        """
        params = [vector, *condition_params, limit]
        with self._connection(context) as connection, connection.cursor() as cursor:
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
                },
            )
            for rank, row in enumerate(rows, start=1)
        ]

    def semantic_status(self, context: RequestContext) -> dict[str, Any]:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT s.id, s.status, count(e.unit_id)::int AS vectors
                FROM search.embedding_spaces s
                LEFT JOIN search.embeddings_1024 e
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

    def vocabulary(self, context: RequestContext, prefix: str, limit: int = 20) -> list[VocabularyItem]:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT token AS term, count(DISTINCT unit_id)::int AS document_frequency, count(*)::int AS corpus_frequency
                FROM search.lexical_units l
                CROSS JOIN LATERAL unnest(string_to_array(l.lexemes, ' ')) AS token
                WHERE l.workspace_id=%s
                  AND token <> ''
                  AND (token ILIKE '%%' || %s || '%%')
                GROUP BY token
                ORDER BY document_frequency DESC, corpus_frequency DESC, token
                LIMIT %s
                """,
                (context.workspace, prefix, limit),
            )
            rows = cursor.fetchall()
        return [VocabularyItem(**dict(row)) for row in rows]

    def get_content_units(self, context: RequestContext, unit_ids: Sequence[str]) -> list[ContentUnit]:
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
                lexical_text=row["lexemes"] or row["body_normalized"],
                locator=EvidenceLocator.model_validate(row["locator"]),
                acl_scopes=list(row["acl_scopes"] or []),
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
                    s.name AS system_name, s.kind AS system_kind,
                    d.id AS document_id, d.stable_key, d.title AS document_title,
                    d.document_type, d.family_key, d.lifecycle, d.metadata AS document_metadata
                FROM content.artifacts a
                JOIN source.revisions r ON r.id=a.revision_id
                JOIN source.objects o ON o.id=r.object_id AND o.current_revision_id=r.id
                JOIN source.systems s ON s.id=o.system_id
                LEFT JOIN content.document_artifacts da ON da.artifact_id=a.id
                LEFT JOIN content.logical_documents d ON d.id=da.document_id
                WHERE a.workspace_id=%s AND a.id=%s
                  AND (cardinality(o.acl_scopes)=0 OR o.acl_scopes <@ %s::text[])
                """,
                (context.workspace, artifact_id, context.acl_scopes),
            )
            row = cursor.fetchone()
        if not row:
            raise NotFoundError(f"artifact not found: {artifact_id}")
        artifact = Artifact(
            id=row["id"], revision_id=row["revision_id"], file_name=row["file_name"],
            extension=row["extension"], media_type=row["media_type"], byte_size=row["byte_size"],
            sha256=row["sha256"], source_path=row["source_path"], cas_uri=row["cas_uri"],
            representation_role=row["representation_role"], metadata=row["metadata"] or {},
        )
        document = None
        if row["document_id"]:
            document = LogicalDocument(
                id=row["document_id"], stable_key=row["stable_key"], title=row["document_title"],
                document_type=row["document_type"], family_key=row["family_key"], lifecycle=row["lifecycle"],
                metadata=row["document_metadata"] or {},
            )
        source_object = SourceObject(
            id=row["object_id"], system_id=row["system_id"], system_name=row["system_name"],
            system_kind=row["system_kind"], external_id=row["external_id"], object_type=row["object_type"],
            canonical_uri=row["canonical_uri"], acl_scopes=list(row["object_acl_scopes"] or []),
            metadata=row["object_metadata"] or {},
        )
        revision = SourceRevision(
            id=row["revision_id"], object_id=row["object_id"], revision_key=row["revision_key"],
            sha256=row["revision_sha256"], size_bytes=row["revision_size_bytes"],
            source_modified_at=row["source_modified_at"], raw_object_uri=row["raw_object_uri"],
            is_tombstone=row["is_tombstone"], metadata=row["revision_metadata"] or {},
        )
        return ArtifactView(artifact=artifact, document=document, source_object=source_object, revision=revision)

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
                ORDER BY da.is_primary DESC, a.file_name
                """,
                (context.workspace, document_id, context.acl_scopes),
            )
            artifacts = cursor.fetchall()
        return {"document": dict(document), "artifacts": [dict(row) for row in artifacts]}

    def graph_neighbors(self, context: RequestContext, request: GraphNeighborsRequest) -> list[GraphEdge]:
        conditions = ["a.workspace_id=%s", "(cardinality(a.acl_scopes)=0 OR a.acl_scopes <@ %s::text[])"]
        params: list[Any] = [context.workspace, context.acl_scopes]
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
            WHERE {' AND '.join(conditions)}
            GROUP BY a.id
            ORDER BY a.created_at DESC
            LIMIT %s
        """
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [self._graph_edge(row) for row in rows]

    def graph_path(self, context: RequestContext, request: GraphPathRequest) -> list[GraphPath]:
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
                  {predicate_clause}
                  AND a.object_entity_id IS NOT NULL
                  AND (cardinality(a.acl_scopes)=0 OR a.acl_scopes <@ %s::text[])
                  AND w.depth < %s
                  AND NOT (CASE WHEN a.subject_id=w.current_node THEN a.object_entity_id ELSE a.subject_id END = ANY(w.node_ids))
            )
            SELECT node_ids, assertion_ids, predicates, depth
            FROM walk
            WHERE current_node=%s AND depth>0
            ORDER BY depth
            LIMIT 20
        """
        params: list[Any] = [request.from_node_id, request.from_node_id, context.workspace]
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
            assertion_id=row["id"], subject_id=row["subject_id"], predicate=row["predicate"],
            object_entity_id=row["object_entity_id"], object_value=row["object_value"], status=row["status"],
            valid_from=row["valid_from"], valid_to=row["valid_to"], ontology_version=row["ontology_version"],
            evidence_unit_ids=list(row["evidence_ids"] or []),
        )

    def enqueue_job(self, context: RequestContext, job_type: str, payload: dict[str, Any], idempotency_key: str | None = None) -> str:
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
            raise DependencyUnavailableError(
                "PostgreSQL did not return the enqueued job"
            )
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
        return JobRecord(id=row["public_id"], job_type=row["job_type"], payload=row["payload"], status=row["status"], attempts=row["attempts"], max_attempts=row["max_attempts"])

    def complete_job(self, context: RequestContext, job_id: str) -> None:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE jobs.queue SET status='succeeded', updated_at=now() WHERE workspace_id=%s AND public_id=%s", (context.workspace, job_id))
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

    def list_jobs(self, context: RequestContext, status: str | None = None, limit: int = 100) -> list[JobRecord]:
        with self._connection(context) as connection, connection.cursor() as cursor:
            if status:
                cursor.execute(
                    "SELECT public_id,job_type,payload,status,attempts,max_attempts FROM jobs.queue WHERE workspace_id=%s AND status=%s ORDER BY id DESC LIMIT %s",
                    (context.workspace, status, limit),
                )
            else:
                cursor.execute(
                    "SELECT public_id,job_type,payload,status,attempts,max_attempts FROM jobs.queue WHERE workspace_id=%s ORDER BY id DESC LIMIT %s",
                    (context.workspace, limit),
                )
            rows = cursor.fetchall()
        return [JobRecord(id=row["public_id"], job_type=row["job_type"], payload=row["payload"], status=row["status"], attempts=row["attempts"], max_attempts=row["max_attempts"]) for row in rows]

    def save_candidate(self, context: RequestContext, candidate: AssertionCandidate) -> AssertionCandidate:
        with self._connection(context) as connection:
            self._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO knowledge.assertion_candidates(
                        id,workspace_id,subject_id,predicate,object_entity_id,object_value,status,origin,confidence,ontology_version,evidence,review_note
                    ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s)
                    ON CONFLICT (id) DO UPDATE SET
                        status=EXCLUDED.status, confidence=EXCLUDED.confidence, evidence=EXCLUDED.evidence, review_note=EXCLUDED.review_note
                    """,
                    (
                        candidate.id, context.workspace, candidate.subject_id, candidate.predicate, candidate.object_entity_id,
                        _json(candidate.object_value) if candidate.object_value is not None else None, candidate.status, candidate.origin,
                        candidate.confidence, candidate.ontology_version, _json(candidate.evidence), candidate.review_note,
                    ),
                )
            connection.commit()
        return candidate

    def list_candidates(self, context: RequestContext, status: str = "proposed", limit: int = 100) -> list[AssertionCandidate]:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM knowledge.assertion_candidates WHERE workspace_id=%s AND status=%s ORDER BY created_at LIMIT %s",
                (context.workspace, status, limit),
            )
            rows = cursor.fetchall()
        return [self._candidate(row) for row in rows]

    def get_candidate(self, context: RequestContext, candidate_id: str) -> AssertionCandidate:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM knowledge.assertion_candidates WHERE workspace_id=%s AND id=%s",
                (context.workspace, candidate_id),
            )
            row = cursor.fetchone()
        if not row:
            raise NotFoundError(f"candidate not found: {candidate_id}")
        return self._candidate(row)

    @staticmethod
    def _candidate(row: dict[str, Any]) -> AssertionCandidate:
        return AssertionCandidate(
            id=row["id"], subject_id=row["subject_id"], predicate=row["predicate"],
            object_entity_id=row["object_entity_id"], object_value=row["object_value"], status=row["status"],
            origin=row["origin"], confidence=row["confidence"], ontology_version=row["ontology_version"],
            evidence=row["evidence"] or [], review_note=row.get("review_note"),
        )

    def approve_candidate(self, context: RequestContext, candidate_id: str, reviewer_id: str, note: str | None = None) -> ApprovedAssertion:
        with self._connection(context) as connection:
            self._ensure_workspace_and_principal(connection, context)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM knowledge.assertion_candidates WHERE workspace_id=%s AND id=%s FOR UPDATE",
                    (context.workspace, candidate_id),
                )
                row = cursor.fetchone()
                if not row:
                    raise NotFoundError(f"candidate not found: {candidate_id}")
                candidate = self._candidate(row)
                if candidate.status != "proposed":
                    raise ConflictError(f"candidate is already {candidate.status}")
                if candidate.predicate in _HIGH_RISK_PREDICATES and not candidate.evidence:
                    raise ValidationError(f"predicate {candidate.predicate} requires evidence")
                evidence_unit_ids = [
                    str(evidence["content_unit_id"])
                    for evidence in candidate.evidence
                    if isinstance(evidence, dict) and evidence.get("content_unit_id")
                ]
                derived_scopes: set[str] = set()
                if evidence_unit_ids:
                    cursor.execute(
                        "SELECT id, acl_scopes FROM content.units WHERE workspace_id=%s AND id = ANY(%s::text[])",
                        (context.workspace, evidence_unit_ids),
                    )
                    evidence_rows = cursor.fetchall()
                    if len(evidence_rows) != len(set(evidence_unit_ids)):
                        raise NotFoundError("one or more evidence units are unavailable")
                    for evidence_row in evidence_rows:
                        derived_scopes.update(evidence_row["acl_scopes"] or [])
                assertion_scopes = sorted(derived_scopes) or list(context.acl_scopes)
                if not set(assertion_scopes).issubset(set(context.acl_scopes)):
                    raise AuthorizationError("reviewer lacks one or more evidence scopes")

                assertion_id = new_id("ast")
                cursor.execute(
                    """
                    INSERT INTO knowledge.assertions(
                        id,workspace_id,subject_id,predicate,object_entity_id,object_value,status,
                        ontology_version,source_candidate_id,acl_scopes,created_by
                    ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,'active',%s,%s,%s,%s)
                    """,
                    (
                        assertion_id, context.workspace, candidate.subject_id, candidate.predicate,
                        candidate.object_entity_id, _json(candidate.object_value) if candidate.object_value is not None else None,
                        candidate.ontology_version, candidate.id, assertion_scopes, reviewer_id,
                    ),
                )
                for evidence in candidate.evidence:
                    unit_id = evidence.get("content_unit_id") if isinstance(evidence, dict) else None
                    if not unit_id:
                        continue
                    locator = evidence.get("locator") or {"type": "content_unit", "data": {"unit_id": unit_id}}
                    cursor.execute(
                        """
                        INSERT INTO knowledge.assertion_evidence(workspace_id, assertion_id, content_unit_id, locator, quote_hash)
                        VALUES (%s,%s,%s,%s::jsonb,%s)
                        """,
                        (context.workspace, assertion_id, unit_id, _json(locator), evidence.get("quote_hash")),
                    )
                cursor.execute(
                    """
                    UPDATE knowledge.assertion_candidates SET status='approved', reviewed_at=now(), reviewed_by=%s, review_note=%s
                    WHERE workspace_id=%s AND id=%s
                    """,
                    (reviewer_id, note, context.workspace, candidate_id),
                )
            connection.commit()
        return ApprovedAssertion(
            id=assertion_id, subject_id=candidate.subject_id, predicate=candidate.predicate,
            object_entity_id=candidate.object_entity_id, object_value=candidate.object_value,
            ontology_version=candidate.ontology_version, source_candidate_id=candidate.id,
            acl_scopes=assertion_scopes, evidence_unit_ids=evidence_unit_ids,
        )

    def reject_candidate(self, context: RequestContext, candidate_id: str, reviewer_id: str, note: str | None = None) -> AssertionCandidate:
        with self._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE knowledge.assertion_candidates
                SET status='rejected', reviewed_at=now(), reviewed_by=%s, review_note=%s
                WHERE workspace_id=%s AND id=%s AND status='proposed'
                RETURNING *
                """,
                (reviewer_id, note, context.workspace, candidate_id),
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
                GROUP BY a.id
                """,
                (context.workspace, assertion_id, context.acl_scopes),
            )
            row = cursor.fetchone()
        if not row:
            raise NotFoundError(f"assertion not found: {assertion_id}")
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
                cursor.execute("DELETE FROM search.lexical_units WHERE workspace_id=%s", (context.workspace,))
                cursor.execute(
                    """
                    INSERT INTO search.lexical_units(
                        unit_id,workspace_id,document_id,artifact_id,source_kind,title,body,lexemes,
                        identifier_text,source_modified_at,source_sha256
                    )
                    SELECT u.id,u.workspace_id,u.document_id,u.artifact_id,s.kind,coalesce(u.title,d.title,''),
                           u.body,u.body_normalized,concat_ws(' ',a.file_name,d.title),r.source_modified_at,r.sha256
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
                result["lexical_units"] = cursor.rowcount
                connection.commit()
        if projection in {"graph", "all"}:
            result["graph"] = "canonical assertions are queried directly; optional graph projection unchanged"
        return result

    def export_canonical(self, context: RequestContext, output: Path) -> dict[str, Any]:
        output.parent.mkdir(parents=True, exist_ok=True)
        exports = [
            ("workspace", "SELECT * FROM kip.workspaces WHERE slug=%s", (context.workspace,)),
            ("source_system", "SELECT * FROM source.systems WHERE workspace_id=%s ORDER BY id", (context.workspace,)),
            ("source_object", "SELECT * FROM source.objects WHERE workspace_id=%s ORDER BY id", (context.workspace,)),
            ("source_revision", "SELECT * FROM source.revisions WHERE workspace_id=%s ORDER BY id", (context.workspace,)),
            ("logical_document", "SELECT * FROM content.logical_documents WHERE workspace_id=%s ORDER BY id", (context.workspace,)),
            ("artifact", "SELECT * FROM content.artifacts WHERE workspace_id=%s ORDER BY id", (context.workspace,)),
            ("content_unit", "SELECT * FROM content.units WHERE workspace_id=%s ORDER BY id", (context.workspace,)),
            ("assertion_candidate", "SELECT * FROM knowledge.assertion_candidates WHERE workspace_id=%s ORDER BY id", (context.workspace,)),
            ("assertion", "SELECT * FROM knowledge.assertions WHERE workspace_id=%s ORDER BY id", (context.workspace,)),
        ]
        count = 0
        with self._connection(context) as connection, output.open("w", encoding="utf-8") as handle:
            for record_type, query, params in exports:
                with connection.cursor() as cursor:
                    cursor.execute(query, params)
                    for row in cursor:
                        handle.write(_json({"type": record_type, "data": dict(row)}) + "\n")
                        count += 1
        return {"output": str(output), "records": count, "generated_at": datetime.now(UTC).isoformat()}
