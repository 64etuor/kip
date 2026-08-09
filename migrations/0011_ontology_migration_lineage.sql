BEGIN;

ALTER TABLE knowledge.assertion_candidates
    ADD COLUMN migrates_assertion_ids text[] NOT NULL DEFAULT ARRAY[]::text[];

CREATE INDEX assertion_candidates_migration_lineage_idx
    ON knowledge.assertion_candidates
    USING gin (migrates_assertion_ids);

COMMIT;
