BEGIN;

-- This transaction-local value is set exclusively by the repository from
-- deployment configuration. SQL NULL/unset denies filesystem sources; JSON
-- null is used only by standalone repositories and trusted ingestion methods.
CREATE OR REPLACE FUNCTION kip.current_filesystem_sources()
RETURNS jsonb LANGUAGE sql STABLE AS $$
    SELECT coalesce(nullif(current_setting('kip.filesystem_sources', true), ''), '[]')::jsonb
$$;

CREATE OR REPLACE FUNCTION kip.filesystem_snapshot_is_allowed(snapshot_id text)
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT kip.current_filesystem_sources() = 'null'::jsonb OR EXISTS (
        SELECT 1 FROM source.acl_snapshots snapshot
        WHERE snapshot.workspace_id = kip.current_workspace_id()
          AND snapshot.id = snapshot_id
          AND (
              snapshot.provider NOT LIKE 'filesystem:%'
              OR EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(
                      CASE WHEN jsonb_typeof(kip.current_filesystem_sources()) = 'array'
                           THEN kip.current_filesystem_sources() ELSE '[]'::jsonb END
                  ) grant_config
                  WHERE grant_config->>'snapshot_id' = snapshot.id
                    AND 'filesystem:' || (grant_config->>'name') = snapshot.provider
              )
          )
    )
$$;

CREATE OR REPLACE FUNCTION kip.acl_snapshot_is_fresh(snapshot_id text)
RETURNS boolean LANGUAGE sql STABLE
SET search_path = pg_catalog, source
AS $$
    SELECT EXISTS (
        SELECT 1 FROM source.acl_snapshots snapshot
        WHERE snapshot.workspace_id = kip.current_workspace_id()
          AND snapshot.id = snapshot_id
          AND (snapshot.configuration_owned OR snapshot.expires_at > statement_timestamp())
          AND kip.filesystem_snapshot_is_allowed(snapshot.id)
    )
$$;

CREATE OR REPLACE FUNCTION kip.source_artifact_is_allowed(artifact_id text)
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT kip.current_filesystem_sources() = 'null'::jsonb OR EXISTS (
        SELECT 1 FROM content.artifacts artifact
        JOIN source.revisions revision ON revision.id = artifact.revision_id
        JOIN source.objects object ON object.id = revision.object_id
        JOIN source.systems system ON system.id = object.system_id
        WHERE artifact.workspace_id = kip.current_workspace_id()
          AND artifact.id = artifact_id
          AND (
              system.kind <> 'filesystem'
              OR EXISTS (
                  SELECT 1 FROM jsonb_array_elements(
                      CASE WHEN jsonb_typeof(kip.current_filesystem_sources()) = 'array'
                           THEN kip.current_filesystem_sources() ELSE '[]'::jsonb END
                  ) grant_config
                  WHERE grant_config->>'name' = system.name
                    AND grant_config->>'snapshot_id' = object.acl_snapshot_id
                    AND left(artifact.source_path, length(rtrim(grant_config->>'root', '/')) + 1)
                        = rtrim(grant_config->>'root', '/') || '/'
                    AND length(artifact.source_path) > length(rtrim(grant_config->>'root', '/')) + 1
                    AND artifact.source_path !~ '/[.]{1,2}(/|$)'
              )
          )
    )
$$;

CREATE OR REPLACE FUNCTION kip.source_assertion_is_allowed(assertion_id text)
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT kip.current_filesystem_sources() = 'null'::jsonb OR NOT EXISTS (
        SELECT 1 FROM knowledge.assertion_evidence evidence
        LEFT JOIN content.units unit ON unit.id = evidence.content_unit_id
        WHERE evidence.workspace_id = kip.current_workspace_id()
          AND evidence.assertion_id = $1
          AND (unit.id IS NULL OR NOT kip.source_artifact_is_allowed(unit.artifact_id))
    )
$$;

COMMIT;
