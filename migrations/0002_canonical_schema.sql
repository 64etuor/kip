BEGIN;

CREATE TABLE IF NOT EXISTS kip.workspaces (
    slug text PRIMARY KEY,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kip.principals (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    kind text NOT NULL,
    external_key text NOT NULL,
    display_name text,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, kind, external_key)
);

CREATE TABLE IF NOT EXISTS source.systems (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    name text NOT NULL,
    kind text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    configuration_fingerprint text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS source.containers (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    system_id text NOT NULL REFERENCES source.systems(id) ON DELETE CASCADE,
    external_id text NOT NULL,
    parent_id text REFERENCES source.containers(id) ON DELETE SET NULL,
    name text NOT NULL,
    container_type text NOT NULL,
    acl_scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, system_id, external_id)
);

CREATE TABLE IF NOT EXISTS source.objects (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    system_id text NOT NULL REFERENCES source.systems(id) ON DELETE CASCADE,
    container_id text REFERENCES source.containers(id) ON DELETE SET NULL,
    external_id text NOT NULL,
    object_type text NOT NULL,
    canonical_uri text NOT NULL,
    current_revision_id text,
    acl_scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, system_id, external_id)
);

CREATE TABLE IF NOT EXISTS source.revisions (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    object_id text NOT NULL REFERENCES source.objects(id) ON DELETE CASCADE,
    revision_key text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    source_modified_at timestamptz,
    sha256 text NOT NULL,
    size_bytes bigint,
    raw_object_uri text,
    is_tombstone boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, object_id, revision_key)
);

ALTER TABLE source.objects
    DROP CONSTRAINT IF EXISTS source_objects_current_revision_fk;
ALTER TABLE source.objects
    ADD CONSTRAINT source_objects_current_revision_fk
    FOREIGN KEY (current_revision_id) REFERENCES source.revisions(id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE IF NOT EXISTS source.sync_cursors (
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    system_id text NOT NULL REFERENCES source.systems(id) ON DELETE CASCADE,
    cursor_name text NOT NULL,
    cursor_value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, system_id, cursor_name)
);

CREATE TABLE IF NOT EXISTS content.logical_documents (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    stable_key text NOT NULL,
    title text NOT NULL,
    document_type text,
    family_key text,
    lifecycle text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, stable_key)
);

CREATE TABLE IF NOT EXISTS content.artifacts (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    revision_id text NOT NULL REFERENCES source.revisions(id) ON DELETE CASCADE,
    file_name text NOT NULL,
    extension text,
    media_type text,
    byte_size bigint,
    sha256 text NOT NULL,
    source_path text,
    cas_uri text,
    representation_role text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, revision_id, sha256, file_name)
);

CREATE TABLE IF NOT EXISTS content.document_artifacts (
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    document_id text NOT NULL REFERENCES content.logical_documents(id) ON DELETE CASCADE,
    artifact_id text NOT NULL REFERENCES content.artifacts(id) ON DELETE CASCADE,
    representation_role text NOT NULL,
    is_primary boolean NOT NULL DEFAULT false,
    PRIMARY KEY (workspace_id, document_id, artifact_id)
);

CREATE TABLE IF NOT EXISTS content.extractions (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    artifact_id text NOT NULL REFERENCES content.artifacts(id) ON DELETE CASCADE,
    parser_name text NOT NULL,
    parser_version text NOT NULL,
    status text NOT NULL,
    active boolean NOT NULL DEFAULT false,
    quality_score double precision,
    output_hash text,
    warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS content_extractions_artifact_active_idx
    ON content.extractions (workspace_id, artifact_id, active);

CREATE TABLE IF NOT EXISTS content.units (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    extraction_id text NOT NULL REFERENCES content.extractions(id) ON DELETE CASCADE,
    document_id text REFERENCES content.logical_documents(id) ON DELETE SET NULL,
    artifact_id text NOT NULL REFERENCES content.artifacts(id) ON DELETE CASCADE,
    ordinal integer NOT NULL,
    unit_type text NOT NULL,
    title text,
    body text NOT NULL,
    body_normalized text NOT NULL,
    locator jsonb NOT NULL,
    acl_scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
    char_count integer NOT NULL DEFAULT 0,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, extraction_id, ordinal)
);
CREATE INDEX IF NOT EXISTS content_units_artifact_idx
    ON content.units (workspace_id, artifact_id);
CREATE INDEX IF NOT EXISTS content_units_document_idx
    ON content.units (workspace_id, document_id);

CREATE TABLE IF NOT EXISTS knowledge.entities (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    entity_type text NOT NULL,
    canonical_name text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    acl_scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS knowledge_entities_name_trgm_idx
    ON knowledge.entities USING gin (canonical_name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS knowledge.entity_identifiers (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    entity_id text NOT NULL REFERENCES knowledge.entities(id) ON DELETE CASCADE,
    namespace text NOT NULL,
    value_display text NOT NULL,
    value_normalized text NOT NULL,
    confidence double precision NOT NULL DEFAULT 1.0,
    verified boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, namespace, value_normalized)
);
CREATE INDEX IF NOT EXISTS entity_identifiers_value_trgm_idx
    ON knowledge.entity_identifiers USING gin (value_normalized gin_trgm_ops);

CREATE TABLE IF NOT EXISTS knowledge.assertion_candidates (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    subject_id text NOT NULL,
    predicate text NOT NULL,
    object_entity_id text,
    object_value jsonb,
    status text NOT NULL DEFAULT 'proposed',
    origin text NOT NULL,
    confidence double precision,
    ontology_version text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    derivation_run_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz,
    reviewed_by text REFERENCES kip.principals(id),
    review_note text,
    CHECK ((object_entity_id IS NOT NULL) <> (object_value IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS assertion_candidates_status_idx
    ON knowledge.assertion_candidates (workspace_id, status, created_at);

CREATE TABLE IF NOT EXISTS knowledge.assertions (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    subject_id text NOT NULL,
    predicate text NOT NULL,
    object_entity_id text,
    object_value jsonb,
    status text NOT NULL DEFAULT 'active',
    valid_from timestamptz,
    valid_to timestamptz,
    ontology_version text NOT NULL,
    source_candidate_id text REFERENCES knowledge.assertion_candidates(id),
    acl_scopes text[] NOT NULL DEFAULT ARRAY[]::text[],
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text REFERENCES kip.principals(id),
    superseded_by text REFERENCES knowledge.assertions(id),
    CHECK ((object_entity_id IS NOT NULL) <> (object_value IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS assertions_subject_idx
    ON knowledge.assertions (workspace_id, subject_id, predicate) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS assertions_object_idx
    ON knowledge.assertions (workspace_id, object_entity_id, predicate) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS knowledge.assertion_evidence (
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    assertion_id text NOT NULL REFERENCES knowledge.assertions(id) ON DELETE CASCADE,
    content_unit_id text NOT NULL REFERENCES content.units(id) ON DELETE RESTRICT,
    locator jsonb NOT NULL,
    quote_hash text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, assertion_id, content_unit_id)
);

CREATE TABLE IF NOT EXISTS search.lexical_units (
    unit_id text PRIMARY KEY REFERENCES content.units(id) ON DELETE CASCADE,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    document_id text,
    artifact_id text NOT NULL,
    source_kind text NOT NULL,
    title text NOT NULL DEFAULT '',
    body text NOT NULL DEFAULT '',
    lexemes text NOT NULL DEFAULT '',
    identifier_text text NOT NULL DEFAULT '',
    source_modified_at timestamptz,
    source_sha256 text NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(lexemes, ''))
    ) STORED,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS lexical_units_tsv_idx
    ON search.lexical_units USING gin (tsv);
CREATE INDEX IF NOT EXISTS lexical_units_title_trgm_idx
    ON search.lexical_units USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS lexical_units_identifier_trgm_idx
    ON search.lexical_units USING gin (identifier_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS lexical_units_document_idx
    ON search.lexical_units (workspace_id, document_id);

CREATE TABLE IF NOT EXISTS search.aliases (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    alias_display text NOT NULL,
    alias_normalized text NOT NULL,
    target_type text NOT NULL,
    target_id text NOT NULL,
    verified boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, alias_normalized, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS search_aliases_trgm_idx
    ON search.aliases USING gin (alias_normalized gin_trgm_ops);

CREATE TABLE IF NOT EXISTS search.embedding_spaces (
    id text PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    name text NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    dimensions integer NOT NULL,
    distance_metric text NOT NULL DEFAULT 'cosine',
    status text NOT NULL DEFAULT 'inactive',
    configuration jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS jobs.queue (
    id bigserial PRIMARY KEY,
    public_id text NOT NULL UNIQUE,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    job_type text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    priority integer NOT NULL DEFAULT 100,
    available_at timestamptz NOT NULL DEFAULT now(),
    locked_at timestamptz,
    locked_by text,
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 5,
    last_error text,
    idempotency_key text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS jobs_queue_claim_idx
    ON jobs.queue (status, available_at, priority, id);

CREATE TABLE IF NOT EXISTS audit.events (
    id bigserial PRIMARY KEY,
    public_id text NOT NULL UNIQUE,
    workspace_id text NOT NULL REFERENCES kip.workspaces(slug) ON DELETE CASCADE,
    actor_id text,
    action text NOT NULL,
    object_type text NOT NULL,
    object_id text,
    request_id text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS kip_workspaces_touch ON kip.workspaces;
CREATE TRIGGER kip_workspaces_touch BEFORE UPDATE ON kip.workspaces
FOR EACH ROW EXECUTE FUNCTION kip.touch_updated_at();
DROP TRIGGER IF EXISTS source_systems_touch ON source.systems;
CREATE TRIGGER source_systems_touch BEFORE UPDATE ON source.systems
FOR EACH ROW EXECUTE FUNCTION kip.touch_updated_at();
DROP TRIGGER IF EXISTS logical_documents_touch ON content.logical_documents;
CREATE TRIGGER logical_documents_touch BEFORE UPDATE ON content.logical_documents
FOR EACH ROW EXECUTE FUNCTION kip.touch_updated_at();
DROP TRIGGER IF EXISTS knowledge_entities_touch ON knowledge.entities;
CREATE TRIGGER knowledge_entities_touch BEFORE UPDATE ON knowledge.entities
FOR EACH ROW EXECUTE FUNCTION kip.touch_updated_at();
DROP TRIGGER IF EXISTS jobs_queue_touch ON jobs.queue;
CREATE TRIGGER jobs_queue_touch BEFORE UPDATE ON jobs.queue
FOR EACH ROW EXECUTE FUNCTION kip.touch_updated_at();

COMMIT;
