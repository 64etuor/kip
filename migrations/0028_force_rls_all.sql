BEGIN;

-- 0016 forced row level security on six core tables and left the rest of the
-- schema ENABLE-only. ENABLE alone is inert for the table owner, so every
-- deployment whose API, worker, or maintenance session connects as the owner
-- kept reading and writing these 18 tables with no workspace or ACL predicate:
--
--   audit.events, audit.query_traces, content.artifacts, content.extractions,
--   content.logical_documents, content.document_artifacts, jobs.queue,
--   kip.principals, knowledge.assertion_candidates,
--   knowledge.assertion_evidence, knowledge.entities,
--   knowledge.entity_identifiers, search.aliases, search.embedding_spaces,
--   search.embeddings_1536, source.containers, source.systems,
--   source.sync_cursors
--
-- Force it everywhere RLS is enabled instead of naming tables, so the
-- invariant "relrowsecurity implies relforcerowsecurity" holds for any table a
-- future migration adds to these schemas as well
-- (tests/integration/test_rls_force.py asserts it).
--
-- Consequences for owner-side maintenance, all of which already applied to the
-- six tables 0016 forced:
--   * Runtime paths are unaffected: every repository connection sets
--     kip.workspace_id / kip.principal_id / kip.acl_scopes / kip.roles before
--     issuing a statement, and projection rebuild takes a RequestContext.
--   * Schema migration stays unaffected because DDL is not policy-checked, but
--     a future migration that backfills rows must set those GUCs (or run as a
--     BYPASSRLS role) instead of relying on owner bypass.
--   * Backup and restore already require it: scripts/backup.sh and
--     scripts/restore.sh export `row_security=off`, which PostgreSQL rejects
--     for a forced table unless the role bypasses RLS. Run them as the
--     BYPASSRLS `kip_backup` role from deploy/sql/roles.sql.template.
--   * Superuser and BYPASSRLS roles still bypass policies entirely; forcing
--     RLS is not a substitute for giving the API and worker the non-owner
--     kip_api / kip_worker roles.

DO $$
DECLARE
    target regclass;
BEGIN
    FOR target IN
        SELECT class_entry.oid::regclass
        FROM pg_class AS class_entry
        JOIN pg_namespace AS namespace_entry
            ON namespace_entry.oid = class_entry.relnamespace
        WHERE class_entry.relkind = 'r'
          AND class_entry.relrowsecurity
          AND NOT class_entry.relforcerowsecurity
          AND namespace_entry.nspname IN (
              'kip', 'source', 'content', 'knowledge',
              'search', 'jobs', 'audit', 'interaction'
          )
        ORDER BY 1
    LOOP
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target);
    END LOOP;
END;
$$;

COMMIT;
