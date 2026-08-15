-- Records whether an assertion candidate was approved by the calibrated
-- auto-approve policy, as an actual column instead of a caller-supplied
-- free-text `review_note` prefix.
--
-- `review_note` is caller-supplied free text on every review action (human
-- approve/reject, and the auto-approve policy's own approval). Any principal
-- with review authority could previously set a `review_note` starting with
-- the auto-approve policy marker on their OWN human decision, which caused
-- `predicate_review_precision` (keyed off `review_note LIKE 'marker%'`) to
-- silently exclude that human decision from its denominator -- a
-- caller-controlled statistic-erasure path. `auto_approved` is written
-- ONLY by `PostgresDatabase.approve_candidate` / `MemoryKnowledgeStore
-- .approve_candidate`, and only when the reviewer identity passed in is the
-- dedicated `AUTO_APPROVE_POLICY_PRINCIPAL` marker (never derived from
-- caller-supplied text), so it cannot be spoofed by a review note.
BEGIN;

ALTER TABLE knowledge.assertion_candidates
    ADD COLUMN IF NOT EXISTS auto_approved boolean NOT NULL DEFAULT false;

COMMIT;
