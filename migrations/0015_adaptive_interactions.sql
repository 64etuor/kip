BEGIN;

CREATE SCHEMA IF NOT EXISTS interaction;

CREATE OR REPLACE FUNCTION kip.current_principal_id()
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('kip.principal_id', true), '')
$$;

CREATE OR REPLACE FUNCTION kip.current_roles()
RETURNS text[] LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN nullif(current_setting('kip.roles', true), '') IS NULL
            THEN ARRAY[]::text[]
        ELSE string_to_array(current_setting('kip.roles', true), ',')
    END
$$;

CREATE OR REPLACE FUNCTION kip.current_is_admin()
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT 'admin' = ANY(kip.current_roles())
$$;

CREATE TABLE interaction.clarifications (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    principal_id text NOT NULL REFERENCES kip.principals(id) ON DELETE CASCADE,
    reason text NOT NULL CHECK (reason IN ('ambiguous_term', 'scope_selection', 'preference', 'other')),
    prompt text NOT NULL CHECK (char_length(prompt) BETWEEN 1 AND 500),
    choices jsonb NOT NULL CHECK (jsonb_typeof(choices) = 'array' AND jsonb_array_length(choices) <= 4),
    allow_freeform boolean NOT NULL,
    allow_multiple boolean NOT NULL,
    preference_key text,
    status text NOT NULL CHECK (status IN ('open', 'answered', 'expired')),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > created_at)
);

CREATE INDEX interaction_clarifications_owner_idx
    ON interaction.clarifications (workspace_id, principal_id, status, expires_at);

CREATE TABLE interaction.preferences (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    principal_id text NOT NULL REFERENCES kip.principals(id) ON DELETE CASCADE,
    preference_key text NOT NULL,
    values jsonb NOT NULL CHECK (jsonb_typeof(values) = 'array' AND jsonb_array_length(values) BETWEEN 1 AND 5),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (workspace_id, principal_id, preference_key)
);

CREATE INDEX interaction_preferences_owner_idx
    ON interaction.preferences (workspace_id, principal_id, preference_key);

CREATE TABLE interaction.feedback (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    principal_id text NOT NULL REFERENCES kip.principals(id) ON DELETE CASCADE,
    request_id text,
    outcome text NOT NULL CHECK (outcome IN ('helpful', 'not_helpful', 'needs_clarification')),
    reason_codes text[] NOT NULL DEFAULT ARRAY[]::text[] CHECK (cardinality(reason_codes) <= 4),
    created_at timestamptz NOT NULL
);

CREATE INDEX interaction_feedback_owner_idx
    ON interaction.feedback (workspace_id, principal_id, created_at DESC);

CREATE TABLE interaction.events (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    principal_id text NOT NULL REFERENCES kip.principals(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN (
        'clarification_answered',
        'preference_saved',
        'preference_deleted',
        'feedback_submitted',
        'ontology_discovery_proposed',
        'ontology_discovery_reviewed'
    )),
    clarification_id text,
    preference_id text,
    feedback_id text,
    candidate_id text,
    outcome text CHECK (outcome IN ('helpful', 'not_helpful', 'needs_clarification')),
    created_at timestamptz NOT NULL
);

CREATE INDEX interaction_events_owner_idx
    ON interaction.events (workspace_id, principal_id, created_at DESC);

CREATE TABLE knowledge.ontology_discovery_candidates (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    submitted_by text NOT NULL REFERENCES kip.principals(id) ON DELETE CASCADE,
    domain_profile text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('entity_type', 'predicate', 'controlled_value', 'alias')),
    symbol text NOT NULL,
    label text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 140),
    definition text NOT NULL CHECK (char_length(definition) BETWEEN 1 AND 500),
    target_symbol text,
    fingerprint text NOT NULL,
    status text NOT NULL CHECK (status IN ('proposed', 'accepted_for_release', 'rejected')),
    occurrence_count integer NOT NULL DEFAULT 1 CHECK (occurrence_count >= 1),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    reviewed_at timestamptz,
    reviewed_by text REFERENCES kip.principals(id) ON DELETE SET NULL,
    review_note text,
    UNIQUE (workspace_id, fingerprint)
);

CREATE INDEX ontology_discovery_candidates_review_idx
    ON knowledge.ontology_discovery_candidates (workspace_id, status, updated_at DESC);

ALTER TABLE interaction.clarifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE interaction.preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE interaction.feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE interaction.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.ontology_discovery_candidates ENABLE ROW LEVEL SECURITY;

ALTER TABLE interaction.clarifications FORCE ROW LEVEL SECURITY;
ALTER TABLE interaction.preferences FORCE ROW LEVEL SECURITY;
ALTER TABLE interaction.feedback FORCE ROW LEVEL SECURITY;
ALTER TABLE interaction.events FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.ontology_discovery_candidates FORCE ROW LEVEL SECURITY;

CREATE POLICY clarification_owner_select ON interaction.clarifications
FOR SELECT
USING (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
);

CREATE POLICY clarification_owner_insert ON interaction.clarifications
FOR INSERT
WITH CHECK (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
);

CREATE POLICY clarification_owner_update ON interaction.clarifications
FOR UPDATE
USING (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
)
WITH CHECK (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
);

CREATE POLICY clarification_owner_delete ON interaction.clarifications
FOR DELETE
USING (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
);

CREATE POLICY clarification_admin_expiry_cleanup ON interaction.clarifications
FOR DELETE
USING (
    workspace_id = kip.current_workspace_id()
    AND kip.current_is_admin()
    AND expires_at < now()
);

CREATE POLICY preference_owner_select ON interaction.preferences
FOR SELECT
USING (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
);

CREATE POLICY preference_owner_insert ON interaction.preferences
FOR INSERT
WITH CHECK (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
);

CREATE POLICY preference_owner_update ON interaction.preferences
FOR UPDATE
USING (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
)
WITH CHECK (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
);

CREATE POLICY preference_owner_delete ON interaction.preferences
FOR DELETE
USING (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
);

CREATE POLICY feedback_owner_select ON interaction.feedback
FOR SELECT
USING (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
);

CREATE POLICY feedback_owner_insert ON interaction.feedback
FOR INSERT
WITH CHECK (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
);

CREATE POLICY event_owner_select ON interaction.events
FOR SELECT
USING (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
);

CREATE POLICY event_owner_insert ON interaction.events
FOR INSERT
WITH CHECK (
    workspace_id = kip.current_workspace_id()
    AND principal_id = kip.current_principal_id()
);

CREATE POLICY discovery_reviewer_select ON knowledge.ontology_discovery_candidates
FOR SELECT
USING (
    workspace_id = kip.current_workspace_id()
    AND kip.current_is_admin()
);

CREATE POLICY discovery_submitter_select ON knowledge.ontology_discovery_candidates
FOR SELECT
USING (
    workspace_id = kip.current_workspace_id()
    AND submitted_by = kip.current_principal_id()
);

CREATE POLICY discovery_submitter_insert ON knowledge.ontology_discovery_candidates
FOR INSERT
WITH CHECK (
    workspace_id = kip.current_workspace_id()
    AND submitted_by = kip.current_principal_id()
);

CREATE POLICY discovery_reviewer_update ON knowledge.ontology_discovery_candidates
FOR UPDATE
USING (
    workspace_id = kip.current_workspace_id()
    AND kip.current_is_admin()
)
WITH CHECK (
    workspace_id = kip.current_workspace_id()
    AND kip.current_is_admin()
);

CREATE POLICY discovery_submitter_increment ON knowledge.ontology_discovery_candidates
FOR UPDATE
USING (
    workspace_id = kip.current_workspace_id()
    AND submitted_by = kip.current_principal_id()
    AND status = 'proposed'
)
WITH CHECK (
    workspace_id = kip.current_workspace_id()
    AND submitted_by = kip.current_principal_id()
    AND status = 'proposed'
);

COMMIT;
