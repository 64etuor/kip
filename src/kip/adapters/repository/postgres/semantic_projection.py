from __future__ import annotations

from dataclasses import dataclass

from kip.adapters.repository.postgres.database import (
    PostgresDatabase,
    _embeddings_union_sql,
)
from kip.domain.embedding import EmbeddingProjectionProgress
from kip.domain.models import (
    EmbeddableUnit,
    RequestContext,
)

# The ACL-freshness and source-policy functions are evaluated once per
# current artifact and per snapshot instead of once per unit: a corpus has
# thousands of files but hundreds of thousands of units, and the per-unit
# calls pushed these projection queries past the statement timeout. The
# predicates are the same ones the search queries apply.
_VISIBLE_SOURCES_CTE = """
    allowed_artifacts AS MATERIALIZED (
        SELECT a.id, r.sha256
        FROM content.artifacts a
        JOIN source.revisions r ON r.id=a.revision_id
        JOIN source.objects o ON o.id=r.object_id AND o.current_revision_id=r.id
        WHERE a.workspace_id=%s AND kip.source_artifact_is_allowed(a.id)
    ),
    fresh_snapshots AS MATERIALIZED (
        SELECT s.id
        FROM source.acl_snapshots s
        WHERE s.workspace_id=%s AND kip.acl_snapshot_is_fresh(s.id)
    )
"""


@dataclass(frozen=True, slots=True)
class PostgresSemanticProjectionStore:
    database: PostgresDatabase

    def list_pending_embeddable_units(
        self,
        context: RequestContext,
        space_id: str,
        *,
        after_unit_id: str | None = None,
        limit: int | None = None,
    ) -> list[EmbeddableUnit]:
        # A space's dimensionality (and therefore its backing table) is not
        # known here, so join against a UNION ALL of every provisioned
        # embeddings table; at most one contributes rows for a given
        # `space_id`.
        embeddings_union = _embeddings_union_sql("workspace_id, unit_id, space_id, source_hash")
        with self.database._connection(context) as connection, connection.cursor() as cursor:
            self.database.projection_timeout(cursor)
            cursor.execute(
                f"""
                WITH {_VISIBLE_SOURCES_CTE}
                SELECT
                    u.id AS unit_id,
                    u.document_id,
                    coalesce(u.title, d.title, '') AS title,
                    u.body_normalized,
                    aa.sha256 AS source_hash
                FROM content.units u
                JOIN content.extractions x ON x.id=u.extraction_id AND x.active
                JOIN allowed_artifacts aa ON aa.id=u.artifact_id
                JOIN fresh_snapshots fs ON fs.id=u.acl_snapshot_id
                LEFT JOIN content.logical_documents d ON d.id=u.document_id
                LEFT JOIN ({embeddings_union}) v
                  ON v.workspace_id=u.workspace_id
                 AND v.unit_id=u.id
                 AND v.space_id=%s
                WHERE u.workspace_id=%s
                  AND (cardinality(u.acl_scopes)=0 OR u.acl_scopes <@ %s::text[])
                  AND (v.unit_id IS NULL OR v.source_hash<>aa.sha256)
                  AND (%s::text IS NULL OR u.id > %s::text)
                ORDER BY u.id
                LIMIT %s
                """,
                (
                    context.workspace,
                    context.workspace,
                    space_id,
                    context.workspace,
                    context.acl_scopes,
                    after_unit_id,
                    after_unit_id,
                    limit,
                ),
            )
            rows = cursor.fetchall()
        return [EmbeddableUnit.model_validate(row) for row in rows]

    def embedding_space_exists(self, context: RequestContext, space_id: str) -> bool:
        with self.database._connection(context) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM search.embedding_spaces
                WHERE workspace_id=%s AND id=%s
                """,
                (context.workspace, space_id),
            )
            return cursor.fetchone() is not None

    def workspace_acl_scopes(self, context: RequestContext) -> list[str]:
        with self.database._connection(context) as connection, connection.cursor() as cursor:
            self.database.projection_timeout(cursor)
            cursor.execute(
                """
                SELECT DISTINCT scope
                FROM source.acl_snapshots s, unnest(s.scopes) AS scope
                WHERE s.workspace_id=%s
                ORDER BY scope
                """,
                (context.workspace,),
            )
            return [str(row["scope"]) for row in cursor.fetchall()]

    def embedding_projection_progress(
        self,
        context: RequestContext,
        space_id: str | None,
    ) -> EmbeddingProjectionProgress:
        embeddings_union = _embeddings_union_sql("workspace_id, unit_id, space_id, source_hash")
        with self.database._connection(context) as connection, connection.cursor() as cursor:
            self.database.projection_timeout(cursor)
            cursor.execute(
                f"""
                WITH {_VISIBLE_SOURCES_CTE}
                SELECT
                    count(*)::int AS content_units,
                    count(v.unit_id) FILTER (WHERE v.source_hash=aa.sha256)::int
                        AS indexed_units
                FROM content.units u
                JOIN content.extractions x ON x.id=u.extraction_id AND x.active
                JOIN allowed_artifacts aa ON aa.id=u.artifact_id
                JOIN fresh_snapshots fs ON fs.id=u.acl_snapshot_id
                LEFT JOIN ({embeddings_union}) v
                  ON v.workspace_id=u.workspace_id
                 AND v.unit_id=u.id
                 AND v.space_id=%s
                WHERE u.workspace_id=%s
                  AND (cardinality(u.acl_scopes)=0 OR u.acl_scopes <@ %s::text[])
                """,
                (context.workspace, context.workspace, space_id, context.workspace, context.acl_scopes),
            )
            row = cursor.fetchone()
        return EmbeddingProjectionProgress.model_validate(row)
