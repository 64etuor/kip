-- Named-file candidate lookups start from the artifact name index (0026) and
-- join back to the caller's visible lexical units by artifact; without this
-- index that join still scanned every lexical unit.
CREATE INDEX IF NOT EXISTS lexical_units_artifact_idx
    ON search.lexical_units (artifact_id);
