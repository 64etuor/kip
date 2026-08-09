BEGIN;

CREATE TABLE audit.query_traces (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    request_id text,
    route text NOT NULL CHECK (route IN ('search', 'context', 'answer', 'ontology_mining')),
    outcome text NOT NULL CHECK (outcome IN ('succeeded', 'refused', 'degraded', 'failed')),
    started_at timestamptz NOT NULL,
    duration_ms double precision NOT NULL CHECK (duration_ms >= 0),
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX query_traces_workspace_started_idx
    ON audit.query_traces (workspace_id, started_at DESC);

CREATE INDEX query_traces_workspace_request_idx
    ON audit.query_traces (workspace_id, request_id)
    WHERE request_id IS NOT NULL;

ALTER TABLE audit.query_traces ENABLE ROW LEVEL SECURITY;

CREATE POLICY workspace_isolation ON audit.query_traces
USING (workspace_id = kip.current_workspace_id())
WITH CHECK (workspace_id = kip.current_workspace_id());

COMMIT;
