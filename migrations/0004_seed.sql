BEGIN;
INSERT INTO kip.workspaces (slug, name)
VALUES ('default', 'Default workspace')
ON CONFLICT (slug) DO NOTHING;
COMMIT;
