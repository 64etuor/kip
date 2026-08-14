-- Filesystem deletion reconciliation: consecutive-complete-scan absence
-- tracking on source objects. A complete, successful scan increments
-- `absent_scan_count` for active objects it did not see and resets it for
-- objects it saw; the application tombstones an object only after the count
-- reaches the configured `sync.deletion_grace_scans`. Canonical rows are
-- never hard-deleted; deletion stays a soft tombstone revision.
BEGIN;

ALTER TABLE source.objects
    ADD COLUMN IF NOT EXISTS absent_scan_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS absent_since timestamptz;

COMMIT;
