# ADR-036: Fix retrieval stage order and gate corpus regressions

- **Status:** Accepted
- **Date:** 2026-08-13

## Context

Search quality changes repeatedly interacted in unexpected ways. Alias
expansion, candidate depth, reranking, fused-tail preservation, diversity, and
truncation were interleaved across mode-specific branches with multiple exits.
A locally plausible heuristic could therefore change another stage's ordering.
The unit suite protected contracts but did not stop a real-corpus retrieval
regression.

Lexical n-grams also returned incidental, score-zero noise for queries whose
entire meaningful vocabulary was absent. However, a broad lexical score
threshold would incorrectly reject low-overlap paraphrases.

## Decision

- Search follows one fixed sequence with one result exit:
  `plan -> analyze -> abstain -> ranked pool -> diversify and truncate`.
  Only ranked-pool construction branches by retrieval mode.
- Candidate limits bound retrieval pools, rerank depth bounds reranker cost,
  and an un-reranked fused tail is preserved before the common diversity and
  request-limit stage.
- The lexical abstention gate returns an empty result only when **every** whole
  content token and approved-alias expansion has zero document frequency in
  the caller's ACL-filtered reachable corpus. One grounded term keeps retrieval
  alive.
- `verify.sh` always invokes a checked-in portable floor containing 100 positive
  search contracts and 20 ACL-negative cases. It also invokes the reviewed
  private-corpus floor for Recall@K, MRR, failed cases, and P95 when that corpus
  is present. Protected runners set `KIP_REQUIRE_PRIVATE_GOLDEN=1`, making a
  missing corpus or skip a hard failure.
- Retrieval-only floors do not certify final-answer correctness, citations, or
  refusals. Those dimensions retain separate reviewed gates.

## Consequences

- Stage changes have a named boundary and a single place where final diversity
  and truncation occur.
- Pure out-of-vocabulary noise now produces an honest empty search and the
  answer service can return `no_admissible_evidence`.
- The gate deliberately does not solve a real-word query with no factual
  answer, partial nonsense anchored by one corpus term, or a semantic
  paraphrase. Those require a calibrated semantic/answer signal.
- Hosted CI blocks portable stage, filter, and ACL regressions without carrying
  private data. A protected corpus-bearing runner is still required to claim
  that real-corpus quality regressions are blocked before merge.

## References

- `src/kip/application/search_engine.py`
- `scripts/golden_gate.py`
- `scripts/portable_golden_gate.py`
- `evaluation/golden/production-regression.yaml`
- `evaluation/golden/private-onedrive-nl.floor.json`
- `docs/PRODUCTION_DESIGN_ALIGNMENT.md`
