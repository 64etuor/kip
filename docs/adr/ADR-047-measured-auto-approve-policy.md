# ADR-047: Measured, audited auto-approve policy for low-risk mined relations

- **Status:** Accepted (owner-approved evolution of architecture rule 10)
- **Date:** 2026-08-15

## Context

Every semantic mining candidate waited for full human review regardless of
predicate risk or measured miner precision, so review throughput capped the
curation loop no matter how good the mining model became. The capability
roadmap flagged this as the constraint most likely to waste model
improvement. Architecture rule 10 previously read "never silently promote";
the owner approved rewording it to "never promote without an audited,
measured, revocable policy; silent promotion is forbidden."

## Decision

1. A relation candidate may be auto-approved only when every axis passes,
   fail-closed:
   - the ontology catalog marks its predicate `review: not_required` AND
     `risk: low` (conditional/required and evidence-required predicates are
     never eligible);
   - the candidate carries `confidence >= ontology.auto_approve.
     min_confidence` (no confidence, no eligibility);
   - measured per-predicate miner precision over HUMAN review decisions is
     `>= min_precision` with at least `min_reviewed` decisions.
     Auto-approved decisions are excluded from their own denominator, so
     the policy cannot reinforce itself; with no data it stays inert.
2. Configuration `[ontology.auto_approve]`: `enabled` (default true),
   `min_precision` 0.95, `min_confidence` 0.8, `min_reviewed` 20 — bounds
   validated at container build; `enabled = false` disables the mechanism
   entirely.
3. Qualifying candidates are approved through the SAME approval path human
   review uses (ontology validation, evidence checks, and supersession
   semantics all apply), with the acting principal
   `auto-approve-policy/v1` and a review note recording the measured
   precision and sample size at decision time
   (`auto-approve-policy/v1 precision=0.9500 sample=20`).
4. Every auto-approval is reported: the mining summary and the durable
   `kip.ontology-mining-result.v1` job payload carry an `auto_approved`
   list (candidate, assertion, predicate, precision, sample size), and the
   marker is visible in candidate listings and assertion explanations.
   Auto-approved assertions are ordinary assertions: `review revoke`
   undoes them exactly like any human approval.
5. Precision is computed from existing candidate status and review-note
   fields (no schema change); memory and Postgres adapters implement the
   identical exclusion rule and are pinned by a behavior-parity contract
   test.

## Consequences

- Review throughput no longer caps the whole loop: human attention
  concentrates on conditional/required and high-risk predicates, while the
  measured floor keeps low-risk auto-approval earned rather than assumed —
  a better miner widens its own auto-approve lane by accumulating human
  agreement first.
- The trust boundary is explicit and inspectable: markers, payload
  reporting, revocability, and self-excluding statistics. Rule 10's new
  wording (AGENTS.md) and the knowledge-fabric skill state the only
  sanctioned non-human path.
- Starter deployments are unaffected until they accumulate 20+ human
  decisions per predicate; the mechanism cannot fire on day one.

## Evidence

- `tests/test_ontology_auto_approve.py` (9 tests: qualifying approval with
  marker and revocability; each axis blocks individually — risk, review
  tier, missing/low confidence, sample floor, precision floor, disabled
  flag; denominator self-exclusion).
- `tests/contract/test_repository_behavior_parity.py::
  test_predicate_review_precision_counts_human_decisions_and_excludes_auto_approve_marker`
  (memory + real Postgres).

## References

- `src/kip/application/ontology_rag.py` (`_maybe_auto_approve`)
- `src/kip/ports/knowledge.py` (`predicate_review_precision`),
  `src/kip/domain/knowledge.py` (`PredicateReviewStats`,
  `AUTO_APPROVE_POLICY_PRINCIPAL`)
- `AGENTS.md` architecture rule 10 (revised wording)
- `docs/plans/2026-08-14-llm-capability-scaling.md` item 4
- ADR-025 (fully-human review baseline this policy calibrates),
  ADR-045 (the sample-audit pattern this generalizes)
