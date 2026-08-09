# ADR-028: Persist redacted RAG decisions and export bounded telemetry

## Status

Accepted

## Context

Production incident review needs to explain retrieval, generation, ontology,
refusal, and latency decisions. Logging queries, evidence bodies, answers,
principals, or provider payloads would create a second sensitive corpus and
make ordinary log access equivalent to document access. A telemetry outage must
also never change an answer or bypass a security decision.

## Decision

KIP persists a closed `kip.query-trace.v1` record in PostgreSQL. It contains
only bounded filter counts, stages, public IDs and ranks, selected evidence and
approved assertion IDs, ACL policy version, pinned model revisions, usage,
controlled warning/refusal codes, outcome, and latency. Raw content and identity
fields are not representable. Untrusted request IDs are omitted.

Workspace RLS protects storage and application inspection additionally requires
an admin role. Retention is explicit and pruned per workspace. Search, context,
answering, and ontology mining call one telemetry use case; persistence and each
exporter fail independently without affecting application semantics.

OTLP/HTTP is optional. The adapter batches spans and periodically exports
metrics to an explicit collector endpoint. OTel attributes contain only route,
outcome, refusal code, counts, schema version, trace ID, stages, and latency;
candidate and evidence IDs remain in canonical admin-only storage.

## Consequences

- Migration `0012` adds RLS-protected canonical query trace storage and indexes
  incident correlation and retention scans.
- CLI and REST expose admin-only inspection and retention pruning.
- The optional telemetry extra installs the official Python SDK and OTLP/HTTP
  exporter; collector credentials remain environment-owned.
- Operators must alert on exporter gaps because delivery is non-blocking by
  design.
