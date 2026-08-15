-- Provisions a second fixed-width pgvector table for a 1536-dimensional
-- embedding space, so switching embedding models to a different
-- dimensionality does not require dynamic DDL. Mirrors 0006
-- (pgvector_1024_projection) + 0018 (embeddings_1024_hnsw); both tables are
-- cheap while empty, so the HNSW index is created here rather than deferred.
BEGIN;

SET LOCAL statement_timeout = 0;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS search.embeddings_1536 (
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    unit_id text NOT NULL REFERENCES content.units(id) ON DELETE CASCADE,
    space_id text NOT NULL REFERENCES search.embedding_spaces(id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL,
    source_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, unit_id, space_id)
);

CREATE INDEX IF NOT EXISTS embeddings_1536_space_idx
    ON search.embeddings_1536 (workspace_id, space_id, unit_id);

ALTER TABLE search.embeddings_1536 ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspace_isolation ON search.embeddings_1536;
CREATE POLICY workspace_isolation ON search.embeddings_1536
USING (workspace_id = kip.current_workspace_id())
WITH CHECK (workspace_id = kip.current_workspace_id());

DROP TRIGGER IF EXISTS embeddings_1536_touch ON search.embeddings_1536;
CREATE TRIGGER embeddings_1536_touch BEFORE UPDATE ON search.embeddings_1536
FOR EACH ROW EXECUTE FUNCTION kip.touch_updated_at();

CREATE INDEX IF NOT EXISTS embeddings_1536_hnsw_cosine_idx
    ON search.embeddings_1536
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);

COMMIT;
