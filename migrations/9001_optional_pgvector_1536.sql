-- Optional migration. Apply only after selecting and documenting a 1536-dimensional embedding space.
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS search.embeddings_1536 (
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    unit_id text NOT NULL REFERENCES content.units(id) ON DELETE CASCADE,
    space_id text NOT NULL REFERENCES search.embedding_spaces(id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL,
    source_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, unit_id, space_id)
);

ALTER TABLE search.embeddings_1536 ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspace_isolation ON search.embeddings_1536;
CREATE POLICY workspace_isolation ON search.embeddings_1536
USING (workspace_id = kip.current_workspace_id())
WITH CHECK (workspace_id = kip.current_workspace_id());

-- Create HNSW only after measuring build time, memory, recall, and filtered-query behavior.
-- CREATE INDEX ... USING hnsw (embedding vector_cosine_ops);
COMMIT;
