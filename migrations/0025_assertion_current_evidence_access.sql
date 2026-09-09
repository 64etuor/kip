BEGIN;

-- Cached assertion scopes may be more restrictive, but must never replace
-- authorization against every evidence unit's current ACL and source policy.
-- Explicit predicates preserve this guarantee for database owners as well.
CREATE OR REPLACE FUNCTION kip.source_assertion_is_allowed(assertion_id text)
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT NOT EXISTS (
        SELECT 1 FROM knowledge.assertion_evidence evidence
        LEFT JOIN content.units unit ON unit.id = evidence.content_unit_id
        WHERE evidence.workspace_id = kip.current_workspace_id()
          AND evidence.assertion_id = $1
          AND (
              unit.id IS NULL
              OR NOT (cardinality(unit.acl_scopes) = 0 OR unit.acl_scopes <@ kip.current_acl_scopes())
              OR NOT kip.acl_snapshot_is_fresh(unit.acl_snapshot_id)
              OR NOT kip.source_artifact_is_allowed(unit.artifact_id)
          )
    )
$$;

COMMIT;
