SELECT jsonb_build_object(
    'schema_version', 'kip.database-backup-manifest.v1',
    'database', current_database(),
    'server_version_num', current_setting('server_version_num')::integer,
    'row_security', current_setting('row_security'),
    'migrations', COALESCE(
        (SELECT jsonb_agg(version ORDER BY version) FROM kip.schema_migrations),
        '[]'::jsonb
    ),
    'extensions', COALESCE(
        (SELECT jsonb_object_agg(extname, extversion ORDER BY extname) FROM pg_extension),
        '{}'::jsonb
    ),
    'rls_policy_count', (SELECT count(*) FROM pg_policies),
    'counts', jsonb_build_object(
        'workspaces', (SELECT count(*) FROM kip.workspaces),
        'principals', (SELECT count(*) FROM kip.principals),
        'source_systems', (SELECT count(*) FROM source.systems),
        'source_objects', (SELECT count(*) FROM source.objects),
        'source_revisions', (SELECT count(*) FROM source.revisions),
        'logical_documents', (SELECT count(*) FROM content.logical_documents),
        'artifacts', (SELECT count(*) FROM content.artifacts),
        'extractions', (SELECT count(*) FROM content.extractions),
        'content_units', (SELECT count(*) FROM content.units),
        'entities', (SELECT count(*) FROM knowledge.entities),
        'entity_candidates', (SELECT count(*) FROM knowledge.entity_candidates),
        'assertion_candidates', (SELECT count(*) FROM knowledge.assertion_candidates),
        'assertions', (SELECT count(*) FROM knowledge.assertions),
        'lexical_units', (SELECT count(*) FROM search.lexical_units),
        'jobs', (SELECT count(*) FROM jobs.queue),
        'audit_events', (SELECT count(*) FROM audit.events),
        'query_traces', (SELECT count(*) FROM audit.query_traces)
    )
)::text;
