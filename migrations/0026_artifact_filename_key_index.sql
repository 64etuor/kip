-- Named-file answers resolve basenames spelled in a question with an exact
-- casefolded equality (see kip.domain.file_references.candidate_basenames).
-- Index the same expression the repository compares so the lookup does not
-- scan every artifact on large corpora.
CREATE INDEX IF NOT EXISTS artifacts_filename_key_idx
    ON content.artifacts (casefold(normalize(file_name, NFC) COLLATE pg_catalog.pg_unicode_fast));
