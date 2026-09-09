# Synchronization and parser operations

## Safety

- Verify the source is mounted and non-empty before reconciliation.
- Never turn a source outage into mass deletion.
- Hash immutable revisions and commit source cursors only after successful ingestion.
- Keep old active extraction until a new extraction passes quality checks and activates atomically.
- Treat Slack edits/deletes and mail placements as revisions or tombstones, not destructive updates.
- Reload services after source configuration changes, then explicitly sync the
  approved retained scope. Old root/snapshot records remain hidden until current
  ingestion establishes evidence; a query never reauthorizes them.
- Cloud placeholders are deferred without hydration. Make only approved files
  local through the provider first; scan skip aggregates are not parser failures
  or deletion evidence.

## XLSX

Index sheet names, dimensions, headers, and string content shallowly. Read candidate workbooks deeply only for exact cell ranges. Do not load every large worksheet into memory.

## HWP/HWPX

Use the parser broker. Keep adapters as subprocesses behind the canonical `DocumentPacket` contract. Benchmark parser changes on a real corpus, preserve prior outputs, and use paired PDF for page evidence when available.

## Scheduling

Use durable jobs and a worker for application-triggered synchronization. Use launchd only as an edge scheduler; job idempotency remains in the repository.
