# ADR-030: Seal complete backups and preserve rebuildable lexical input

## Status

Accepted

## Context

A PostgreSQL dump alone does not prove that RLS-protected rows were visible to
the backup role, and a CAS hash list without the objects cannot be restored.
The earlier lexical rebuild also copied normalized body text instead of the
Korean analyzer output used during ingestion. A successful restore could
therefore preserve row counts while silently lowering retrieval quality.

## Decision

Every backup captures PostgreSQL with `row_security=off`, a global count and
migration manifest, a canonical workspace export, the complete CAS archive and
hash manifest, and a configuration/ontology/migration snapshot. A manifest and
complete checksum file seal the set. Restore accepts only an explicitly
confirmed empty database and absent or empty CAS target, then compares canonical
counts, migrations, extension versions, RLS policy count, and every CAS hash.

`ContentUnit.lexical_text` is persisted as canonical extraction output. Migration
0013 backfills it from the active lexical projection, falling back to normalized
body text only when no projection row exists. Lexical rebuild uses a temporary
staging relation and changes only divergent or stale rows, so an unchanged
projection is idempotent and does not create table/index bloat.

A restore drill additionally rebuilds projections, regenerates PostgreSQL
planner statistics, reruns a caller-supplied immutable golden dataset, compares
decision-relevant results with the pre-backup baseline, and seals a redacted
recovery receipt.

## Consequences

- Backup and restore require PostgreSQL client tools or the reference Compose
  PostgreSQL service with matching major version.
- Private configuration, canonical exports, dumps, CAS objects, and drill
  evidence remain operational artifacts and are excluded from starter releases.
- Large projection rebuilds use an explicit bounded restore timeout without
  weakening the normal query timeout.
- A golden comparison can prove restore fidelity but does not turn an
  inadequate baseline into a production-quality RAG evaluation.
