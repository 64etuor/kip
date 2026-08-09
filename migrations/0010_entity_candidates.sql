BEGIN;

CREATE TABLE knowledge.entity_candidates (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    fingerprint text NOT NULL,
    entity_type text NOT NULL,
    canonical_name text NOT NULL,
    aliases text[] NOT NULL DEFAULT ARRAY[]::text[],
    status text NOT NULL DEFAULT 'proposed',
    origin text NOT NULL,
    confidence double precision,
    ontology_version text NOT NULL,
    evidence jsonb NOT NULL,
    derivation jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz,
    reviewed_by text REFERENCES kip.principals(id),
    review_note text,
    UNIQUE (workspace_id, fingerprint),
    CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    CHECK (jsonb_typeof(evidence) = 'array' AND jsonb_array_length(evidence) > 0)
);

CREATE INDEX entity_candidates_status_idx
    ON knowledge.entity_candidates (workspace_id, status, created_at);

CREATE TABLE knowledge.entity_candidate_evidence (
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    candidate_id text NOT NULL REFERENCES knowledge.entity_candidates(id) ON DELETE CASCADE,
    content_unit_id text NOT NULL REFERENCES content.units(id) ON DELETE RESTRICT,
    source_revision_sha256 text NOT NULL,
    locator jsonb NOT NULL,
    quote_hash text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, candidate_id, content_unit_id)
);

CREATE INDEX entity_candidate_evidence_unit_idx
    ON knowledge.entity_candidate_evidence (workspace_id, content_unit_id);

ALTER TABLE knowledge.entity_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.entity_candidates FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_evidence_acl ON knowledge.entity_candidates
USING (
    workspace_id = kip.current_workspace_id()
    AND NOT EXISTS (
        SELECT 1
        FROM jsonb_array_elements(entity_candidates.evidence) item
        WHERE NOT EXISTS (
            SELECT 1
            FROM content.units unit
            WHERE unit.workspace_id = entity_candidates.workspace_id
              AND unit.id = item->>'content_unit_id'
        )
    )
)
WITH CHECK (workspace_id = kip.current_workspace_id());

ALTER TABLE knowledge.entity_candidate_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.entity_candidate_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_evidence_acl ON knowledge.entity_candidate_evidence
USING (
    workspace_id = kip.current_workspace_id()
    AND EXISTS (
        SELECT 1
        FROM content.units unit
        WHERE unit.workspace_id = entity_candidate_evidence.workspace_id
          AND unit.id = entity_candidate_evidence.content_unit_id
    )
)
WITH CHECK (workspace_id = kip.current_workspace_id());

DROP POLICY IF EXISTS workspace_isolation ON knowledge.assertion_candidates;
CREATE POLICY workspace_evidence_acl ON knowledge.assertion_candidates
USING (
    workspace_id = kip.current_workspace_id()
    AND NOT EXISTS (
        SELECT 1
        FROM jsonb_array_elements(assertion_candidates.evidence) item
        WHERE NOT EXISTS (
            SELECT 1
            FROM content.units unit
            WHERE unit.workspace_id = assertion_candidates.workspace_id
              AND unit.id = item->>'content_unit_id'
        )
    )
)
WITH CHECK (workspace_id = kip.current_workspace_id());

COMMIT;
