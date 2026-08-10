BEGIN;

-- Table owners bypass ENABLE-only row level security, so a deployment that
-- connects as the table owner (the reference compose profile) silently ran
-- with every core-table policy inert. Forcing RLS makes the workspace and
-- ACL-scope policies effective for all roles, including the owner.
--
-- Maintenance paths that must bypass policies (sealed backups, migration
-- backfills) already run with an explicit SET row_security = off, which
-- remains available to the table owner and BYPASSRLS roles.

ALTER TABLE source.objects FORCE ROW LEVEL SECURITY;
ALTER TABLE source.revisions FORCE ROW LEVEL SECURITY;
ALTER TABLE content.units FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.assertions FORCE ROW LEVEL SECURITY;
ALTER TABLE search.lexical_units FORCE ROW LEVEL SECURITY;
ALTER TABLE search.embeddings_1024 FORCE ROW LEVEL SECURITY;

COMMIT;
