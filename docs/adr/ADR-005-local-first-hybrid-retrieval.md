# ADR-005: Local-first Hybrid Retrieval Is Evaluation-gated

- **Status:** Accepted
- **Date:** 2026-07-30
- **Decision owners:** KIP maintainers

## Context

KIP has a measured PostgreSQL lexical baseline but no golden evaluator, real embedding adapter, pgvector query path, fusion, or reranker. Loading transformer models inside every CLI process would create large cold-start and memory costs, while a managed API would conflict with the default no-egress security posture.

## Decision

KIP will use a provider-neutral HTTP boundary and a loopback-only persistent model sidecar for local embeddings and reranking. PostgreSQL pgvector stores versioned, disposable semantic projections. Lexical and vector ranks are combined with reciprocal-rank fusion; a reranker may reorder only the bounded fused candidate set.

The first candidates are Qwen3 Embedding 0.6B and BGE-M3 at 1024 dimensions, with BGE reranker v2 M3. Model names do not constitute adoption. A configuration becomes active only after the checked-in golden evaluator proves the TRD quality, latency, stale-source, and zero-unauthorized-result gates.

Initial real-corpus testing uses only explicitly licensed public Korean government documents. OneDrive and company cloud storage are outside the source allowlist until the operator names a specific directory.

Neo4j remains outside this decision and implementation scope.

The first public-pilot decision is to keep semantic search disabled. PostgreSQL
lexical retrieval reached Recall@10 and MRR of 1.000 on all 30 authorized
questions with zero ACL leaks. Qwen3 vector and hybrid retrieval reached
Recall@10 1.000 but lower MRR, and BGE reranking did not improve the hybrid
ranking. The complete 74-vector semantic space remains in `shadow` status for
future, harder corpora.

### 2026-08-13 amendment

Promotion is evaluated per retrieval variant, not by forcing every vector
space through hybrid and reranked composition. On the reviewed 19-case private
set, vector-only Recall@10/MRR (`0.947/0.822`) exceeded hybrid
(`0.895/0.702`) and reranked (`0.842/0.656`). The candidate therefore remains
vector-only for future gate work. After ADR-037's HNSW path and the
ACL-prefiltered index-scan correction, the same aggregate and category quality
metrics hold at P95 `133.75 ms`, with zero failed cases and zero ACL leaks. It
is still disabled because stale-warning coverage is missing. A quality or
latency win does not waive the remaining gate or activate a space.

## Consequences

- The core package remains usable without PyTorch or a model server.
- Semantic projections can be rebuilt or compared without altering canonical content.
- CLI/API processes share a warmed local model service.
- Operators must manage one optional loopback process and model cache.
- The supported PostgreSQL production profile carries the 1024-dimensional
  HNSW index from ADR-037. Exact-search comparisons remain mandatory evidence
  for ANN recall and filtered-candidate sufficiency.
- Reports and the evolution ledger provide reproducible improvement evidence, but promotion remains an explicit human decision.

## References

- `docs/plans/2026-07-30-rag-quality-stack-design.md`
- `docs/plans/2026-07-30-rag-quality-stack-implementation.md`
- `docs/TRD.md` sections 36.9 through 36.11
