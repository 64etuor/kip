BEGIN;

SET LOCAL statement_timeout = 0;

CREATE INDEX IF NOT EXISTS embeddings_1024_hnsw_cosine_idx
    ON search.embeddings_1024
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);

COMMIT;
