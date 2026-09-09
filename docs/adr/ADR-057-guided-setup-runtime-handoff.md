# ADR-057: Approved setup controls the effective runtime

- Status: Accepted
- Date: 2026-09-10

## Context

Layering generated Compose over the example retained unapproved sample mounts.
Bootstrap defaults could shadow approved identity/workspace settings, while
database URLs and custom secret references resolved differently across host
commands and containers. Configuration verification alone did not expose all
missing credentials, cloud-only sources, or an uninstalled generation service.

## Decision

Generate a complete standalone Compose project and select it alone through
`app-up.sh`. API, worker, and migration share the selected database and exact
approved source mounts. The default database reference selects bundled local
PostgreSQL and validates matching host credentials; a custom reference selects
the external-database topology. Startup orders database readiness/migration
before application services.

Source mount targets use the same canonical absolute host paths in containers,
so both generated configs share roots, file URIs, and ACL snapshot identities
when they use one database. Only managed CAS/backup paths are translated.
Reject duplicate source targets, source/model-secret overlap, and collisions
with protected runtime directories. Older split-namespace plans must be
regenerated and their approved sources explicitly resynced; never rewrite old
evidence provenance to make it appear authorized under a different path.

Repository wrappers prefer the generated host config over bootstrap defaults,
while preserving explicit exported overrides and a custom config selection.
Secret lookup follows the configured variable name. Fresh bootstrap generates
random private credentials; it does not rotate an existing deployment.
Record the installer's non-root UID/GID and supplementary groups in the plan
fingerprint and services; apply verifies the current host memberships.
Old plans remain readable but must be regenerated before apply or host transfer.

The generated launcher resolves secret files and URL-encodes the container DB
connection without writing credentials into generated artifacts. Migration
mounts the approved CAS writable because initialization creates its directory,
but mounts no sources. An absent production or explicitly configured custom
database reference fails closed rather than silently selecting memory storage.

Accept folder paths as source-answer shorthand with conservative classification
and workspace ACL defaults, then require preview and concrete plan approval.
Preview reports local/cloud-only counts using metadata. Runtime verification
resolves secrets, rejects placeholders and identical API/admin keys, checks
source availability, and reports local generation provisioning as incomplete.

## Consequences

Generated artifacts must match their plan; stale/incomplete handoffs fail
instead of silently falling back. `app-up.sh` is the first receipt next step,
followed by explicit sync, search, and exact read. Local generation, scheduled
sync, external identity/TLS/secrets, backup policy, and corpus-quality acceptance
still need deployment-specific work. Setup enables neither automatic source
downloads nor model/relation promotion.

The runtime smoke wrapper uses the approved config's port and resolved API key,
the project interpreter, and `/readyz` plus capabilities. It sends no caller
identity headers. App startup reports the configured host port and readiness URL.
