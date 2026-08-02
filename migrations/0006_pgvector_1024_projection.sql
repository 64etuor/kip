BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE search.embedding_spaces
    ADD COLUMN IF NOT EXISTS model_revision text NOT NULL DEFAULT 'unknown';
ALTER TABLE search.embedding_spaces
    ADD COLUMN IF NOT EXISTS normalized boolean NOT NULL DEFAULT true;

CREATE UNIQUE INDEX IF NOT EXISTS embedding_spaces_one_active_idx
    ON search.embedding_spaces (workspace_id)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS search.embeddings_1024 (
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    unit_id text NOT NULL REFERENCES content.units(id) ON DELETE CASCADE,
    space_id text NOT NULL REFERENCES search.embedding_spaces(id) ON DELETE CASCADE,
    embedding vector(1024) NOT NULL,
    source_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, unit_id, space_id)
);

CREATE INDEX IF NOT EXISTS embeddings_1024_space_idx
    ON search.embeddings_1024 (workspace_id, space_id, unit_id);

ALTER TABLE search.embeddings_1024 ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workspace_isolation ON search.embeddings_1024;
CREATE POLICY workspace_isolation ON search.embeddings_1024
USING (workspace_id = kip.current_workspace_id())
WITH CHECK (workspace_id = kip.current_workspace_id());

DROP TRIGGER IF EXISTS embeddings_1024_touch ON search.embeddings_1024;
CREATE TRIGGER embeddings_1024_touch BEFORE UPDATE ON search.embeddings_1024
FOR EACH ROW EXECUTE FUNCTION kip.touch_updated_at();

COMMIT;
