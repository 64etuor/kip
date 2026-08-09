BEGIN;

CREATE TABLE source.acl_snapshots (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    provider text NOT NULL,
    snapshot_version text NOT NULL,
    captured_at timestamptz NOT NULL,
    expires_at timestamptz,
    configuration_owned boolean NOT NULL DEFAULT false,
    scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
    scope_mapping jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    CHECK (
        (configuration_owned AND expires_at IS NULL)
        OR (
            NOT configuration_owned
            AND expires_at IS NOT NULL
            AND expires_at > captured_at
        )
    )
);

CREATE INDEX source_acl_snapshots_active_idx
    ON source.acl_snapshots (workspace_id, configuration_owned, expires_at);
CREATE INDEX source_acl_snapshots_stale_idx
    ON source.acl_snapshots (workspace_id, expires_at)
    WHERE expires_at IS NOT NULL;

ALTER TABLE source.objects ADD COLUMN acl_snapshot_id text;
ALTER TABLE content.units ADD COLUMN acl_snapshot_id text;
ALTER TABLE knowledge.assertions
    ADD COLUMN evidence_acl_snapshot_ids text[] NOT NULL DEFAULT ARRAY[]::text[];

INSERT INTO source.acl_snapshots (
    id,
    workspace_id,
    provider,
    snapshot_version,
    captured_at,
    configuration_owned,
    scopes,
    scope_mapping
)
SELECT
    'aclsnap_' || md5(o.workspace_id || chr(31) || o.id || chr(31) || array_to_string(o.acl_scopes, ',')),
    o.workspace_id,
    'legacy-configuration',
    'migration-0007',
    o.first_seen_at,
    true,
    o.acl_scopes,
    jsonb_build_object('migration', '0007')
FROM source.objects o
ON CONFLICT (id) DO NOTHING;

UPDATE source.objects o
SET acl_snapshot_id =
    'aclsnap_' || md5(o.workspace_id || chr(31) || o.id || chr(31) || array_to_string(o.acl_scopes, ','));

UPDATE content.units u
SET acl_snapshot_id = o.acl_snapshot_id
FROM content.artifacts a
JOIN source.revisions r ON r.id = a.revision_id
JOIN source.objects o ON o.id = r.object_id
WHERE a.id = u.artifact_id
  AND a.workspace_id = u.workspace_id
  AND o.workspace_id = u.workspace_id;

ALTER TABLE source.objects ALTER COLUMN acl_snapshot_id SET NOT NULL;
ALTER TABLE content.units ALTER COLUMN acl_snapshot_id SET NOT NULL;

UPDATE knowledge.assertions assertion
SET evidence_acl_snapshot_ids = snapshot_ids.ids
FROM (
    SELECT
        evidence.assertion_id,
        array_agg(DISTINCT unit.acl_snapshot_id ORDER BY unit.acl_snapshot_id) AS ids
    FROM knowledge.assertion_evidence evidence
    JOIN content.units unit ON unit.id = evidence.content_unit_id
    GROUP BY evidence.assertion_id
) snapshot_ids
WHERE assertion.id = snapshot_ids.assertion_id;

ALTER TABLE source.objects
    ADD CONSTRAINT source_objects_acl_snapshot_fk
    FOREIGN KEY (workspace_id, acl_snapshot_id)
    REFERENCES source.acl_snapshots(workspace_id, id)
    ON DELETE RESTRICT;
ALTER TABLE content.units
    ADD CONSTRAINT content_units_acl_snapshot_fk
    FOREIGN KEY (workspace_id, acl_snapshot_id)
    REFERENCES source.acl_snapshots(workspace_id, id)
    ON DELETE RESTRICT;

CREATE INDEX source_objects_acl_snapshot_idx
    ON source.objects (workspace_id, acl_snapshot_id);
CREATE INDEX content_units_acl_snapshot_idx
    ON content.units (workspace_id, acl_snapshot_id);

ALTER TABLE source.acl_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE source.acl_snapshots FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON source.acl_snapshots
USING (workspace_id = kip.current_workspace_id())
WITH CHECK (workspace_id = kip.current_workspace_id());

CREATE OR REPLACE FUNCTION kip.acl_snapshot_is_fresh(snapshot_id text)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog, source
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM source.acl_snapshots snapshot
        WHERE snapshot.workspace_id = kip.current_workspace_id()
          AND snapshot.id = snapshot_id
          AND (
              snapshot.configuration_owned
              OR snapshot.expires_at > statement_timestamp()
          )
    )
$$;

DROP POLICY IF EXISTS workspace_acl ON source.objects;
CREATE POLICY workspace_acl ON source.objects
USING (
    workspace_id = kip.current_workspace_id()
    AND (cardinality(acl_scopes)=0 OR acl_scopes <@ kip.current_acl_scopes())
    AND kip.acl_snapshot_is_fresh(acl_snapshot_id)
)
WITH CHECK (workspace_id = kip.current_workspace_id());

DROP POLICY IF EXISTS workspace_acl ON content.units;
CREATE POLICY workspace_acl ON content.units
USING (
    workspace_id = kip.current_workspace_id()
    AND (cardinality(acl_scopes)=0 OR acl_scopes <@ kip.current_acl_scopes())
    AND kip.acl_snapshot_is_fresh(acl_snapshot_id)
)
WITH CHECK (workspace_id = kip.current_workspace_id());

DROP POLICY IF EXISTS workspace_acl ON knowledge.assertions;
CREATE POLICY workspace_acl ON knowledge.assertions
USING (
    workspace_id = kip.current_workspace_id()
    AND (cardinality(acl_scopes)=0 OR acl_scopes <@ kip.current_acl_scopes())
    AND NOT EXISTS (
        SELECT 1
        FROM unnest(evidence_acl_snapshot_ids) snapshot_id
        WHERE NOT kip.acl_snapshot_is_fresh(snapshot_id)
    )
)
WITH CHECK (workspace_id = kip.current_workspace_id());

COMMIT;
