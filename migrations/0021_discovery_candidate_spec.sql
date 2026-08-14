-- Persist the full ontology discovery proposal spec (parent, domain, range,
-- inverse, risk, review, extraction) so a candidate reviewed after a process
-- restart materializes with its originally submitted spec instead of the
-- release-time fallback defaults. `target_symbol` continues to be persisted
-- and mirrored as before for backward compatibility; `proposal_spec` is the
-- lossless source of truth the store now reads back into
-- `OntologyDiscoveryCandidate.parent`/`domain`/`range`/`inverse`/`risk`/
-- `review`/`extraction`.
-- NULL means "no spec recorded" (legacy rows written before this migration,
-- or a proposal with no predicate/entity_type spec fields set) and must
-- behave exactly like today: `ontology_discovery_release.py` falls back to
-- its safe defaults at materialization time.
BEGIN;

ALTER TABLE knowledge.ontology_discovery_candidates
    ADD COLUMN IF NOT EXISTS proposal_spec jsonb;

COMMIT;
