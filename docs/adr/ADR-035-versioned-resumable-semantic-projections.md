# ADR-035: Version semantic inputs and resume projection rebuilds

- **Status:** Accepted
- **Date:** 2026-08-13

## Context

The private OneDrive projection contains more than 30,000 current units. A
full restart re-embedded already valid rows, while a few oversized legacy units
could stall a batch. Completeness also used a broad content count that did not
necessarily match the current revision, active extraction, ACL-fresh rows the
vector query could actually return. Those behaviors made interruption recovery
expensive and made `projection verify` an unreliable activation guard.

Embedding preprocessing changes semantic meaning. Reusing one space after
changing truncation or input composition would silently mix incompatible
vectors even when the model and dimensions remained the same.

## Decision

- The current document input is `title + normalized body`, bounded by
  `models.embedding.max_document_chars` (default 4,000).
- Oversized bodies use the named `head_tail_v1` strategy: preserve the title,
  then sample both the head and tail around an explicit truncation marker.
- The input cap and strategy are part of the embedding-space configuration,
  stable ID, and visible name. Operational batch size and timeout are not part
  of semantic identity.
- A rebuild selects only units from the current source revision and active
  extraction whose ACL snapshot is fresh and visible to the projection request
  context. It embeds rows that are missing or whose stored source hash differs,
  sorts pending inputs by bounded length, and upserts each completed batch.
- Rebuild progress, `projection verify`, and activation use that same eligible
  denominator. Extra rows from superseded revisions or inactive extractions do
  not make a current space complete or incomplete.
- Completion never activates a space. Fingerprint-matched evaluation and a
  separate operator activation remain mandatory.

## Consequences

- Interrupted builds resume without paying the full embedding cost again.
- Preprocessing changes create a parallel shadow space instead of corrupting an
  existing one.
- Completeness is meaningful only for the request context used to build and
  verify the space; operators must use the approved projection workspace and
  scopes.
- The bounded input avoids sidecar stalls but can omit middle content. Richer
  source-aware chunking is a future evaluated design change, not an implicit
  extension of `head_tail_v1`.
- The 2026-08-13 private space completed at 30,565/30,565. HNSW vector P95 is
  133.75 ms with preserved quality, but the space remains disabled because the
  stale-warning gate has no observation and fails closed.

## References

- `docs/TRD.md` section 23
- `docs/OPERATIONS.md` local semantic shadow runbook
- `evaluation/reports/semantic-qwen3-all-modes-final-20260813/decision.md`
