-- Revocation audit fields for approved assertions.
-- `status` transitions to 'revoked' are recorded with a required note and
-- actor; the assertion row itself is never deleted. `superseded_by` already
-- exists from 0002 and records approve-with-supersede transitions.
BEGIN;

ALTER TABLE knowledge.assertions
    ADD COLUMN IF NOT EXISTS revoked_at timestamptz,
    ADD COLUMN IF NOT EXISTS revoked_by text REFERENCES kip.principals(id),
    ADD COLUMN IF NOT EXISTS revocation_note text;

COMMIT;
