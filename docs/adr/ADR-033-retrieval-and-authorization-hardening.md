# ADR-033: Retrieval and authorization hardening

- Status: accepted
- Date: 2026-08-10

## Context

A full design and performance review found five structural defects that the
existing gates did not catch:

1. The default reranked search mode returned at most `rerank_candidate_limit`
   results because the un-reranked fused tail was discarded.
2. An answer-time lexical keyword gate dropped paraphrase evidence surfaced by
   semantic retrieval before the structured generator could see it, and
   contained query-specific approval heuristics.
3. REST handlers were `async def` around fully blocking psycopg and file I/O,
   serializing the event loop, and every repository call opened a new TCP
   connection.
4. The CLI granted itself the `admin` role, which since migration 0015 also
   satisfied PostgreSQL RLS policies through the `kip.roles` GUC; core tables
   used `ENABLE` (not `FORCE`) RLS, so owner-role deployments bypassed every
   policy.
5. Lexical search predicates were written in forms no index could serve, the
   vector ordering blocked any future ANN index, sync re-hashed every file and
   rewrote workspace-wide ACL rows per file, and two evaluation metrics
   (`latest_version_accuracy`, `stale_warning_rate`) read metadata nothing
   populated.

## Decision

- Rerank depth bounds rerank cost only; the fused tail is appended up to the
  request limit. The starter rerank depth is 40.
- The lexical relevance gate and approval heuristics apply only to the
  extractive fallback, which quotes evidence verbatim. The generation path
  receives all fresh ACL-filtered evidence and relies on claim-citation
  validation and typed refusals.
- REST route handlers are synchronous (threadpool-executed) and the PostgreSQL
  adapter uses a bounded `psycopg_pool.ConnectionPool`
  (`database.pool_max_size`, default 10). Per-checkout GUC setup is one round
  trip.
- The CLI never fabricates roles. Operator roles come only from explicit
  `--role`/`--roles`/`KIP_ROLES`, matching the MCP adapter; admin commands
  fail closed without them. Migration 0016 adds `FORCE ROW LEVEL SECURITY` to
  `source.objects`, `source.revisions`, `content.units`,
  `knowledge.assertions`, `search.lexical_units`, and
  `search.embeddings_1024`, so the policy layer holds even for owner-role
  connections. Maintenance paths keep using explicit `row_security = off`.
- Lexical predicates use `ILIKE` and the trigram `%` operator (threshold set
  per query), migration 0017 adds the missing body trigram index, and vector
  search orders by the bare cosine distance so the optional HNSW index
  (migration 9002, not auto-applied) is usable once measured and accepted.
- Sync skips unchanged files by comparing size and `mtime_ns` against the
  stored current revision before hashing; file hashes are computed lazily on
  first use. The per-file settle sleep is replaced by an mtime settle window.
  The ACL snapshot upsert skips the unit and assertion cascade when snapshot
  identity, scopes, and classification are unchanged.
- Search hits carry a store-computed `is_latest` flag (newest source file
  backing the logical document). The evaluation runner accepts a hit
  enricher, wired by the CLI to reopen evidence outside the latency window,
  making `stale_warning_rate` measurable instead of permanently failing.

## Consequences

- Retrieval quality work (semantic activation, reranker comparisons) now
  measures the full result list and two previously vacuous or unsatisfiable
  gate metrics are real.
- Owner-role reference deployments lose their silent RLS bypass; any future
  migration that backfills core tables must set `row_security = off`
  explicitly.
- Files modified within the settle window are picked up on the next scan
  rather than after an in-scan sleep.
- A future BM25-style ranking change and HNSW adoption remain open; this ADR
  only removes the structural blockers.
