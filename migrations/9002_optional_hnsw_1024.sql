-- Optional migration: approximate nearest-neighbour index for the 1024d
-- semantic projection. Not applied automatically (9xxx prefix).
--
-- Apply only after measuring exact-scan latency on the target corpus and
-- accepting approximate recall. The adapter orders by the bare cosine
-- distance expression, so this index is used as soon as it exists.
--
-- Operational notes:
-- * ACL and freshness predicates filter AFTER the ANN candidate scan; raise
--   hnsw.ef_search (for example SET hnsw.ef_search = 100) so enough
--   candidates survive filtering.
-- * Rebuilding an embedding space rewrites all rows; build the index after a
--   bulk load, not before.

BEGIN;

CREATE INDEX IF NOT EXISTS embeddings_1024_hnsw_cosine_idx
    ON search.embeddings_1024 USING hnsw (embedding vector_cosine_ops);

COMMIT;
