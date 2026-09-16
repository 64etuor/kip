-- Bring the pgvector catalog version up to the library the server runs.
--
-- A Compose deployment that pulls a newer pgvector/pgvector image keeps its
-- volume, and the volume keeps the extension version it was created with:
-- pg_extension.extversion stays at 0.8.2 while the server loads the newer
-- shared library. The HNSW vacuum fixes of 0.8.3-0.8.4 live in that library and
-- apply on restart; the 0.8.x update scripts change no SQL objects. Updating
-- the catalog keeps extversion truthful for `kip doctor`, the backup manifest
-- and restore verification, which record and compare it.
--
-- Idempotent: nothing runs when extversion already equals the version this
-- server ships. ALTER EXTENSION ... UPDATE is transactional, so it runs inside
-- the migration runner's implicit transaction.
--
-- Never fails the migration. A migration role that does not own the extension
-- (an externally managed PostgreSQL where a DBA created it), a catalog newer
-- than the image, and another session holding the extension past a 5-second
-- lock timeout all leave the catalog unchanged with a server WARNING. The
-- runner re-checks the version after every migrate, outside the ledger, and
-- reports what it could not update in the envelope's meta.warnings.
--
-- No REINDEX here. The on-disk HNSW format is unchanged, so existing indexes
-- keep working; pgvector publishes no REINDEX requirement for these fixes.
-- REINDEX INDEX CONCURRENTLY cannot run inside this multi-statement migration,
-- a plain REINDEX blocks writes and reads through the index for its duration,
-- and either one is bounded by the migration statement timeout. An index that
-- was damaged before the upgrade is repaired by the operator step in
-- docs/TROUBLESHOOTING.md instead.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_extension AS installed
        JOIN pg_available_extensions AS available ON available.name = installed.extname
        WHERE installed.extname = 'vector'
          AND available.default_version IS DISTINCT FROM installed.extversion
    ) THEN
        BEGIN
            PERFORM set_config('lock_timeout', '5s', true);
            ALTER EXTENSION vector UPDATE;
        EXCEPTION
            WHEN insufficient_privilege OR invalid_parameter_value
                 OR lock_not_available OR query_canceled THEN
                RAISE WARNING USING MESSAGE = 'vector extension not updated: ' || SQLERRM;
        END;
    END IF;
END
$$;
