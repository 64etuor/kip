# ADR-068: pgvector extension upgrades and restore compatibility

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

The bundled PostgreSQL image was pinned to `pgvector/pgvector:0.8.2-pg18-trixie`.
pgvector 0.8.3 and 0.8.4 fix HNSW index corruption when VACUUM runs alongside
INSERT, and KIP uses HNSW indexes (migrations 0018 and 0022) under sync upsert
and delete churn. A further INSERT/VACUUM race (pgvector #1010) is merged but
unreleased; it ships in 0.8.7.

The upgrade was deferred in 3.15.4 because three facts combined to break
existing deployments:

- A Compose volume keeps its catalog `extversion` when the image changes, so a
  database created on 0.8.2 still reports 0.8.2 on a 0.8.6 server until
  `ALTER EXTENSION vector UPDATE` runs.
- `pg_restore` recreates extensions at the target server's default version.
- Restore verification required identical extension versions, so a backup
  taken at 0.8.2 failed verification when restored onto a 0.8.6 server.

Two further gaps affect deployments that already exist:

- `.env.example` sets `KIP_POSTGRES_IMAGE`, and an existing `.env` overrides the
  Compose default, so updating KIP alone never changed the image.
- A migration runs once. If new code migrated before the image changed, a
  one-time update migration would record itself as a no-op and leave the
  catalog behind.

## Decision

1. Pin `pgvector/pgvector:0.8.6-pg18-trixie` by multi-arch digest. Keep
   PostgreSQL major 18. Adopting 0.8.7 when it is released is the same pin bump.
2. Bring the catalog forward in two places. Migration `0029` runs
   `ALTER EXTENSION vector UPDATE` only when the installed version differs from
   the server's default. Every `kip migrate` also runs the same idempotent step
   after pending migrations, outside the ledger, so the order in which code and
   image change does not matter. The migrate result reports what it updated.
3. Never REINDEX in a migration. The fix lives in the shared library loaded at
   server start, and the on-disk index format is unchanged. `REINDEX
   CONCURRENTLY` cannot run inside a transaction, and a plain REINDEX blocks the
   index. Repairing damage created before the upgrade is a documented operator
   step: rebuild the semantic projection, or run `REINDEX INDEX CONCURRENTLY`.
4. Restore verification accepts only `vector` restored at a newer patch of the
   same `0.8` line than the backup, and records it as `extension_updates`. Any
   other extension difference still fails, including a different set, a
   downgrade or another minor or major line. Updating the target before
   comparing was rejected: `pg_restore` already creates the newest version, so
   it would change nothing.
5. The upgrade and bootstrap paths replace `KIP_POSTGRES_IMAGE` in an existing
   `.env` only when its value is exactly an image KIP itself shipped before.
   Any other value is a deliberate choice, so it is left in place with a
   warning. `kip doctor` reports a catalog older than the server as a
   non-required check.

## Consequences

- Existing deployments move to the fixed library through `kip update` followed
  by `./scripts/app-up.sh`, on the same volume, with no data migration.
- Backups taken at 0.8.2 stay restorable after the upgrade.
- A deployment that pinned a custom PostgreSQL image keeps it and must update
  that image itself.
- The #1010 race remains until 0.8.7. Its symptom is lower recall, not data
  loss, and the embeddings are a rebuildable projection.
