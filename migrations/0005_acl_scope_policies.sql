BEGIN;

-- Workspace isolation remains universal. Tables that carry required ACL scopes
-- additionally require every stored scope to be present in the session scope set.
-- Runtime adapters also apply the same predicate before search and traversal.

DROP POLICY IF EXISTS workspace_isolation ON source.containers;
CREATE POLICY workspace_acl ON source.containers
USING (
    workspace_id = kip.current_workspace_id()
    AND (cardinality(acl_scopes)=0 OR acl_scopes <@ kip.current_acl_scopes())
)
WITH CHECK (workspace_id = kip.current_workspace_id());

DROP POLICY IF EXISTS workspace_isolation ON source.objects;
CREATE POLICY workspace_acl ON source.objects
USING (
    workspace_id = kip.current_workspace_id()
    AND (cardinality(acl_scopes)=0 OR acl_scopes <@ kip.current_acl_scopes())
)
WITH CHECK (workspace_id = kip.current_workspace_id());

DROP POLICY IF EXISTS workspace_isolation ON content.units;
CREATE POLICY workspace_acl ON content.units
USING (
    workspace_id = kip.current_workspace_id()
    AND (cardinality(acl_scopes)=0 OR acl_scopes <@ kip.current_acl_scopes())
)
WITH CHECK (workspace_id = kip.current_workspace_id());

DROP POLICY IF EXISTS workspace_isolation ON knowledge.entities;
CREATE POLICY workspace_acl ON knowledge.entities
USING (
    workspace_id = kip.current_workspace_id()
    AND (cardinality(acl_scopes)=0 OR acl_scopes <@ kip.current_acl_scopes())
)
WITH CHECK (workspace_id = kip.current_workspace_id());

DROP POLICY IF EXISTS workspace_isolation ON knowledge.assertions;
CREATE POLICY workspace_acl ON knowledge.assertions
USING (
    workspace_id = kip.current_workspace_id()
    AND (cardinality(acl_scopes)=0 OR acl_scopes <@ kip.current_acl_scopes())
)
WITH CHECK (workspace_id = kip.current_workspace_id());

COMMIT;
