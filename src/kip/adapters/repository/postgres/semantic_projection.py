from __future__ import annotations

from dataclasses import dataclass

from kip.adapters.repository.postgres.database import PostgresDatabase
from kip.domain.embedding import EmbeddingProjectionProgress
from kip.domain.models import (
    EmbeddableUnit,
    RequestContext,
)


@dataclass(frozen=True, slots=True)
class PostgresSemanticProjectionStore:
    database: PostgresDatabase

    def list_pending_embeddable_units(
        self,
        context: RequestContext,
        space_id: str,
    ) -> list[EmbeddableUnit]:
        with self.database._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    u.id AS unit_id,
                    u.document_id,
                    coalesce(u.title, d.title, '') AS title,
                    u.body_normalized,
                    r.sha256 AS source_hash
                FROM content.units u
                JOIN content.extractions x ON x.id=u.extraction_id AND x.active
                JOIN content.artifacts a ON a.id=u.artifact_id
                JOIN source.revisions r ON r.id=a.revision_id
                JOIN source.objects o ON o.id=r.object_id AND o.current_revision_id=r.id
                LEFT JOIN content.logical_documents d ON d.id=u.document_id
                LEFT JOIN search.embeddings_1024 v
                  ON v.workspace_id=u.workspace_id
                 AND v.unit_id=u.id
                 AND v.space_id=%s
                WHERE u.workspace_id=%s
                  AND (cardinality(u.acl_scopes)=0 OR u.acl_scopes <@ %s::text[])
                  AND kip.acl_snapshot_is_fresh(u.acl_snapshot_id)
                  AND (v.unit_id IS NULL OR v.source_hash<>r.sha256)
                ORDER BY u.id
                """,
                (space_id, context.workspace, context.acl_scopes),
            )
            rows = cursor.fetchall()
        return [EmbeddableUnit.model_validate(row) for row in rows]

    def embedding_projection_progress(
        self,
        context: RequestContext,
        space_id: str | None,
    ) -> EmbeddingProjectionProgress:
        with self.database._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    count(*)::int AS content_units,
                    count(v.unit_id) FILTER (WHERE v.source_hash=r.sha256)::int
                        AS indexed_units
                FROM content.units u
                JOIN content.extractions x ON x.id=u.extraction_id AND x.active
                JOIN content.artifacts a ON a.id=u.artifact_id
                JOIN source.revisions r ON r.id=a.revision_id
                JOIN source.objects o ON o.id=r.object_id AND o.current_revision_id=r.id
                LEFT JOIN search.embeddings_1024 v
                  ON v.workspace_id=u.workspace_id
                 AND v.unit_id=u.id
                 AND v.space_id=%s
                WHERE u.workspace_id=%s
                  AND (cardinality(u.acl_scopes)=0 OR u.acl_scopes <@ %s::text[])
                  AND kip.acl_snapshot_is_fresh(u.acl_snapshot_id)
                """,
                (space_id, context.workspace, context.acl_scopes),
            )
            row = cursor.fetchone()
        return EmbeddingProjectionProgress.model_validate(row)
