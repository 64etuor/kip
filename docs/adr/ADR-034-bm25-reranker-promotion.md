# ADR-034: Promote the candidate-local BM25 reranker

- Status: accepted
- Date: 2026-08-11

## Context

ADR-031 promoted RapidFuzz as the starter lexical reranker on a
source-derived query set that contained no reviewed natural-language
cases, and deferred BM25-class engines (ParadeDB, PGroonga) pending
corpus evidence. A candidate-local Okapi BM25 reranker (no model,
extension, or schema dependency) was since implemented as an opt-in
shadow backend, and a 19-case natural-language golden set was grounded
in the private OneDrive corpus, adversarially re-verified case by case
by an independent agent (18/19 pass; the one ambiguous identifier case
was widened to both legitimate targets), and promoted to
`reviewed 1.0.0` with a corpus fingerprint. Promotion authority for the
dataset review was explicitly delegated by the workspace owner in the
2026-08-11 session.

## Decision

`models.reranker.backend` defaults to `bm25`. RapidFuzz remains the
supported fallback backend, and the BGE/Jina model adapters remain
opt-in shadow candidates.

On the reviewed set, with alias expansion, the long-query bound,
per-document diversity, and the overlap-chunked HWP corpus all active
(`evaluation/reports/final-config-20260811/`):

| config | Recall@10 | MRR | nDCG@10 | failed | P95 |
|---|---:|---:|---:|---:|---:|
| rapidfuzz + expansion | 0.737 | 0.576 | 0.616 | 0 | 7.93s |
| **bm25 + expansion (chosen)** | **0.789** | **0.646** | **0.680** | 0 | 7.86s |
| bm25 without expansion | 0.789 | 0.640 | 0.678 | 0 | 6.57s |

Against the active RapidFuzz configuration this clears the promotion
thresholds (overall recall +0.052 against a +0.03 gate) with no
category-level regression and comparable latency. Alias expansion stays
enabled: under bm25 it is aggregate-neutral-to-positive (MRR +0.006,
one paraphrase case fully solved, one identifier-adjacent case worse)
and clearly positive under the RapidFuzz fallback.

## Consequences

- Answer and context quality inherit the better ordering; the
  extractive-answer wrong-citation failure observed in blind testing was
  caused by ranking and shrinks accordingly.
- BM25 statistics are candidate-local; if candidate generation changes
  materially (semantic channel activation, corpus-global df), re-run
  `kip evaluate run` on `evaluation/golden/private-onedrive-nl.yaml`
  before keeping the backend.
- ParadeDB/PGroonga remain deferred: the reranker-level BM25 captured
  the ranking win without the operational cost, and the remaining
  paraphrase gap (recall 0.43 in that category) is a candidate-recall
  problem owned by the semantic channel, not by ranking.
