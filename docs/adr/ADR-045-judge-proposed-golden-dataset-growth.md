# ADR-045: Judge-proposed golden-dataset growth with human sample-audit

- **Status:** Accepted
- **Date:** 2026-08-14

## Context

Only fully-human review could create canonical evaluation truth, so the
reviewed golden sets stayed tiny (the private reviewed set is 19 cases).
That bottleneck cascades: semantic retrieval wins every measured quality
metric (Recall@10 0.947 vs lexical 0.789) but stays disabled because the
`stale_warning_rate` gate has no observations, and no one has the review
bandwidth to grow the dataset that would produce them. As models improve,
this is the single constraint that most prevents the system from absorbing
that improvement: a better model has no channel through which to prove
itself.

The invariant to preserve is unchanged: an LLM judge cannot create
canonical truth or auto-promote anything.

## Decision

1. A judge — an external agent or the configured generation model — may
   author `kip.golden-draft.v1` proposals: golden-dataset cases (the exact
   `GoldenCase` shape) plus per-case `judge_confidence` and `rationale`,
   with judge provenance (`judge_kind`, model, revision) on the draft.
   Drafts are immutable proposals with no status field.
2. A human sample-audits the draft: `kip evaluate draft review` records
   per-case approve/reject decisions in a `kip.golden-draft-review.v1`
   file bound to the exact draft bytes by SHA-256 fingerprint and to a
   named reviewer.
3. `kip evaluate draft promote` performs fail-closed canonicalization:
   the review must bind to the current draft fingerprint; sampled coverage
   must reach `--min-sample-rate` (default 0.2); any rejected sampled case
   fails the entire batch (the human triages the draft, no cherry-picking);
   promoted IDs must not collide with the target dataset; promoted cases
   are stripped of judge-only fields, re-validated through the real
   golden-dataset loader, and carry a
   `judge-proposed (<judge_kind>), sample-audited by <reviewer>` marker in
   their notes.
4. Canonical-authority fields are assigned at promotion, never proposed: a
   draft whose cases set `lifecycle`, `version`, `reviewer`, or
   `source_revision` is rejected outright (a judge may not forge review
   authority or gate eligibility). Promotion assigns them explicitly —
   `--lifecycle` (default `reviewed`, `draft` refused), `--dataset-version`
   (required for a fresh dataset, defaults to an existing dataset's
   non-draft version, `draft` refused), `--source-revision` (defaults to
   the draft's `corpus_fingerprint`, otherwise required), and
   `reviewer = review.reviewer` — so a correctly promoted dataset satisfies
   `gate_eligible` and the recorded reviewer is always the human who ran
   the sample audit. Review and dataset files are written atomically
   (temp file + rename).
4. Draft and review schemas are published under `contracts/`
   (`golden-draft.schema.json`, `golden-draft-review.schema.json`).

## Consequences

- Golden datasets can grow at judge speed while truth-creation authority
  stays with the human reviewer; a bad judge batch is rejected wholesale,
  which is itself a measurement of judge quality.
- The path to activating semantic retrieval becomes an evidence task:
  draft stale-source cases, sample-audit, promote, measure
  `stale_warning_rate`, activate if the gate passes.
- Rejected samples give per-batch judge precision data — the input the
  future calibrated-review-tier work (see
  `docs/plans/2026-08-14-llm-capability-scaling.md`) needs.
- Known limitation (accepted trust model): the review file's decision list
  has no self-integrity protection beyond the draft fingerprint binding.
  An actor with local filesystem write access could hand-edit recorded
  decisions; the pipeline assumes a single trusted operator on a trusted
  filesystem, with git history as the audit trail. Signing review files is
  future work if multi-operator deployments need it.

## Evidence

- `tests/test_evaluation_drafts.py` (16 tests: schema validation, review
  fingerprint binding, promotion happy paths through the real dataset
  loader, low-sample-rate/rejected-case/ID-collision refusals, CLI
  round-trip).

## References

- `src/kip/evaluation/drafts.py`
- `evaluation/golden/drafts/README.md`, `example-draft.yaml`
- `docs/RAG_EVALUATION.md` (judge section)
- `docs/plans/2026-08-14-llm-capability-scaling.md`
