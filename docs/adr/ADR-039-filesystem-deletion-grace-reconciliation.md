# ADR-039: Reconcile filesystem deletions with a complete-scan grace policy

- **Status:** Accepted
- **Date:** 2026-08-13

## Context

The target design (PRD FR-SRC-004, TRD sections 14.1 and 14.3) required that
files deleted from a filesystem source eventually leave the active index
without ever interpreting a source outage as mass deletion. The implementation
had no deletion path at all: a file removed from the NAS stayed searchable and
citable forever, while event connectors (Slack) already had immutable
tombstone-revision semantics.

## Decision

1. Track absence canonically: migration `0020` adds `absent_scan_count` and
   `absent_since` to source objects, and the ingestion port gains
   `reconcile_scan_absences`.
2. Only a complete, successful scan contributes deletion evidence. A failed or
   aborted scan marks nothing. A scan that sees zero files is treated as a
   possible mount outage: reconciliation is skipped entirely and a warning is
   recorded on the sync summary. Dry-run syncs never mark or tombstone.
3. After each complete scan, every active indexed object the scan did not see
   increments its consecutive-absence counter (`SyncSummary.absent`). An
   object absent for `[sync] deletion_grace_scans` consecutive complete scans
   (default 2, bounded 1-100) is soft-deleted through the same shared
   `ingest_packet` tombstone-revision path used by event connectors
   (`SyncSummary.tombstoned`): a zero-unit tombstone revision becomes current,
   removing the object from search, context, and ontology evidence while
   preserving prior revisions, extraction history, and approved assertions.
4. A file that reappears — or that was seen but failed to parse — clears its
   absence mark; a reappearing tombstoned file is re-indexed by the next sync.
   Nothing is ever hard-deleted and the source tree is never written.

## Consequences

- Deleted NAS files stop being presented as current evidence after the grace
  window, with an auditable revision history instead of row deletion.
- Empty scans never contribute deletion evidence, so a truly emptied source
  tree is never tombstoned automatically; that requires operator action.
- Narrowing `include_extensions` or `exclude_globs` tombstones now-out-of-scope
  content after the grace window; collection-scope changes need review before
  the next sync.
- The TRD's additional sentinel-file, count-drop, and permission-ratio mount
  guards remain unimplemented and are recorded as a limitation in
  `docs/IMPLEMENTATION_STATUS.md`.

## References

- `docs/PRD.md` (FR-SRC-004, FR-SRC-007)
- `docs/TRD.md` (sections 14.1, 14.3)
- `docs/CONNECTORS.md`
- `docs/OPERATIONS.md` ("Filesystem deletion grace policy")
- `migrations/0020_source_object_absence.sql`
- `contracts/sync-summary.schema.json`
