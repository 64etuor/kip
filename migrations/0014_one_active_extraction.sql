BEGIN;

WITH ranked_active AS (
    SELECT
        id,
        row_number() OVER (
            PARTITION BY workspace_id, artifact_id
            ORDER BY completed_at DESC NULLS LAST, started_at DESC, id DESC
        ) AS active_rank
    FROM content.extractions
    WHERE active
)
UPDATE content.extractions extraction
SET active = false
FROM ranked_active ranked
WHERE extraction.id = ranked.id
  AND ranked.active_rank > 1;

CREATE UNIQUE INDEX IF NOT EXISTS content_one_active_extraction_per_artifact
    ON content.extractions (workspace_id, artifact_id)
    WHERE active;

COMMIT;
