BEGIN;

ALTER TABLE content.units
    ADD COLUMN lexical_text text;

UPDATE content.units AS unit
SET lexical_text = lexical.lexemes
FROM search.lexical_units AS lexical
WHERE lexical.unit_id = unit.id;

UPDATE content.units
SET lexical_text = body_normalized
WHERE lexical_text IS NULL;

ALTER TABLE content.units
    ALTER COLUMN lexical_text SET NOT NULL;

COMMIT;
