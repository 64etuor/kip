BEGIN;

ALTER TABLE knowledge.entities
    ADD COLUMN canonical_name_normalized text,
    ADD COLUMN aliases text[] NOT NULL DEFAULT ARRAY[]::text[];

UPDATE knowledge.entities
SET canonical_name_normalized = lower(regexp_replace(trim(canonical_name), '\s+', ' ', 'g'));

ALTER TABLE knowledge.entities
    ALTER COLUMN canonical_name_normalized SET NOT NULL;

CREATE INDEX knowledge_entities_normalized_name_idx
    ON knowledge.entities (workspace_id, entity_type, canonical_name_normalized);

CREATE TABLE knowledge.entity_aliases (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    entity_id text NOT NULL REFERENCES knowledge.entities(id) ON DELETE CASCADE,
    alias_display text NOT NULL,
    alias_normalized text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, alias_normalized)
);

CREATE INDEX knowledge_entity_aliases_entity_idx
    ON knowledge.entity_aliases (workspace_id, entity_id);

INSERT INTO knowledge.entity_aliases (
    id,
    workspace_id,
    entity_id,
    alias_display,
    alias_normalized
)
SELECT
    'alias_' || md5(entity.workspace_id || chr(31) || entity.id || chr(31) || entity.canonical_name_normalized),
    entity.workspace_id,
    entity.id,
    entity.canonical_name,
    entity.canonical_name_normalized
FROM knowledge.entities entity
ON CONFLICT (workspace_id, alias_normalized) DO NOTHING;

ALTER TABLE knowledge.assertion_candidates
    ADD COLUMN fingerprint text,
    ADD COLUMN valid_from timestamptz,
    ADD COLUMN valid_to timestamptz,
    ADD COLUMN derivation jsonb,
    ADD COLUMN review_risk text NOT NULL DEFAULT 'medium',
    ADD COLUMN contradicts_assertion_ids text[] NOT NULL DEFAULT ARRAY[]::text[];

UPDATE knowledge.assertion_candidates
SET fingerprint = 'legacy:' || md5(
    workspace_id || chr(31) || id || chr(31) || predicate || chr(31) || evidence::text
)
WHERE fingerprint IS NULL;

ALTER TABLE knowledge.assertion_candidates
    ALTER COLUMN fingerprint SET NOT NULL,
    ADD CONSTRAINT assertion_candidates_fingerprint_unique
        UNIQUE (workspace_id, fingerprint),
    ADD CONSTRAINT assertion_candidates_valid_interval
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
    ADD CONSTRAINT assertion_candidates_review_risk
        CHECK (review_risk IN ('low', 'medium', 'high'));

ALTER TABLE knowledge.assertions
    ADD CONSTRAINT assertions_valid_interval
        CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from);

CREATE TABLE knowledge.assertion_candidate_evidence (
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    candidate_id text NOT NULL REFERENCES knowledge.assertion_candidates(id) ON DELETE CASCADE,
    content_unit_id text NOT NULL REFERENCES content.units(id) ON DELETE RESTRICT,
    source_revision_sha256 text NOT NULL,
    locator jsonb NOT NULL,
    quote_hash text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, candidate_id, content_unit_id)
);

CREATE INDEX assertion_candidate_evidence_unit_idx
    ON knowledge.assertion_candidate_evidence (workspace_id, content_unit_id);

INSERT INTO knowledge.assertion_candidate_evidence (
    workspace_id,
    candidate_id,
    content_unit_id,
    source_revision_sha256,
    locator,
    quote_hash
)
SELECT
    candidate.workspace_id,
    candidate.id,
    unit.id,
    revision.sha256,
    coalesce(
        evidence.item->'locator',
        jsonb_build_object(
            'type', 'content_unit',
            'data', jsonb_build_object('unit_id', unit.id)
        )
    ),
    evidence.item->>'quote_hash'
FROM knowledge.assertion_candidates candidate
CROSS JOIN LATERAL jsonb_array_elements(candidate.evidence) AS evidence(item)
JOIN content.units unit
  ON unit.workspace_id = candidate.workspace_id
 AND unit.id = evidence.item->>'content_unit_id'
JOIN content.artifacts artifact
  ON artifact.workspace_id = unit.workspace_id
 AND artifact.id = unit.artifact_id
JOIN source.revisions revision
  ON revision.workspace_id = artifact.workspace_id
 AND revision.id = artifact.revision_id
ON CONFLICT DO NOTHING;

ALTER TABLE knowledge.entity_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.entity_aliases FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_entity_acl ON knowledge.entity_aliases
USING (
    workspace_id = kip.current_workspace_id()
    AND EXISTS (
        SELECT 1
        FROM knowledge.entities entity
        WHERE entity.workspace_id = entity_aliases.workspace_id
          AND entity.id = entity_aliases.entity_id
    )
)
WITH CHECK (workspace_id = kip.current_workspace_id());

ALTER TABLE knowledge.assertion_candidate_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.assertion_candidate_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_evidence_acl ON knowledge.assertion_candidate_evidence
USING (
    workspace_id = kip.current_workspace_id()
    AND EXISTS (
        SELECT 1
        FROM content.units unit
        WHERE unit.workspace_id = assertion_candidate_evidence.workspace_id
          AND unit.id = assertion_candidate_evidence.content_unit_id
    )
)
WITH CHECK (workspace_id = kip.current_workspace_id());

COMMIT;
