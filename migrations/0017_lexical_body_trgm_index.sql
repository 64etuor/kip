BEGIN;

-- The lexical search body substring arm previously forced a sequential scan:
-- the predicate wrapped columns in lower() (not matched by the raw-column
-- trigram indexes) and body had no index at all. The adapter now issues
-- ILIKE / trigram-operator predicates, which this index makes BitmapOr-able
-- together with the existing title, identifier, and tsv indexes.

CREATE INDEX IF NOT EXISTS lexical_units_body_trgm_idx
    ON search.lexical_units USING gin (body gin_trgm_ops);

COMMIT;
