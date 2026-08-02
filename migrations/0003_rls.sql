BEGIN;

CREATE OR REPLACE FUNCTION kip.current_workspace_id()
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('kip.workspace_id', true), '')
$$;

CREATE OR REPLACE FUNCTION kip.current_acl_scopes()
RETURNS text[] LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN nullif(current_setting('kip.acl_scopes', true), '') IS NULL
            THEN ARRAY[]::text[]
        ELSE string_to_array(current_setting('kip.acl_scopes', true), ',')
    END
$$;

DO $$
DECLARE
    target regclass;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'kip.principals'::regclass,
        'source.systems'::regclass,
        'source.containers'::regclass,
        'source.objects'::regclass,
        'source.revisions'::regclass,
        'source.sync_cursors'::regclass,
        'content.logical_documents'::regclass,
        'content.artifacts'::regclass,
        'content.document_artifacts'::regclass,
        'content.extractions'::regclass,
        'content.units'::regclass,
        'knowledge.entities'::regclass,
        'knowledge.entity_identifiers'::regclass,
        'knowledge.assertion_candidates'::regclass,
        'knowledge.assertions'::regclass,
        'knowledge.assertion_evidence'::regclass,
        'search.lexical_units'::regclass,
        'search.aliases'::regclass,
        'search.embedding_spaces'::regclass,
        'jobs.queue'::regclass,
        'audit.events'::regclass
    ] LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target);
        EXECUTE format('DROP POLICY IF EXISTS workspace_isolation ON %s', target);
        EXECUTE format(
            'CREATE POLICY workspace_isolation ON %s USING (workspace_id = kip.current_workspace_id()) WITH CHECK (workspace_id = kip.current_workspace_id())',
            target
        );
    END LOOP;
END;
$$;

COMMIT;
