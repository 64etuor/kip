# Production checklist

The default Compose profile is a local pilot profile. Before serving multiple users or indexing sensitive mail/Slack data:

## Documentation and acceptance evidence

- Review `PRODUCTION_DESIGN_ALIGNMENT.md` and treat every unresolved row as a
  gate or an explicitly accepted pilot limitation.
- Update PRD/TRD, contracts, operations, security, implementation status,
  examples, and ADRs in the same change whenever their behavior or design
  changes. A dated acceptance report is historical evidence, not current status.
- Deploy the supported PostgreSQL reference profile with pgvector migration 0006
  and the 1024-dimensional HNSW migration 0018. Keep semantic activation a
  separate reviewed decision.
- Require the 120-case portable search/ACL gate on every merge. Treat a skipped
  private golden gate as missing evidence; set
  `KIP_REQUIRE_PRIVATE_GOLDEN=1` on the approved corpus-bearing runner so an
  unavailable corpus fails the merge.

## Identity and database

- Create separate migration, worker, read-agent, reviewer, and backup roles.
- Do not run API or worker with `kip_owner`.
- Apply and review `deploy/sql/roles.sql.template` as the object owner; bind
  platform login roles to its NOLOGIN groups.
- Give only the backup role verified `BYPASSRLS`; keep API, worker, and reviewer
  roles `NOBYPASSRLS`.
- Put API behind an organization-approved identity-aware proxy.
- Derive workspace, principal, and ACL scopes from trusted identity; do not trust arbitrary client headers at an internet-facing boundary.
- Run RLS tests using non-owner roles and verify inaccessible graph paths return no evidence of existence.

## Network and secrets

- Keep PostgreSQL, Neo4j, and MCP stdio off public interfaces.
- Bind the reference API to loopback or a private network.
- Store secrets in the platform secret manager, not `.env` in production.
- Materialize secret references as absolute, operator-only, regular one-line
  files; reject symlinks and trailing multiline payloads.
- Deny model egress unless a provider and data classification are explicitly approved.

## Sources

- Mount NAS read-only and add a source-outage guard before any absence reconciliation.
- Use Slack conversation allowlists and confirm token scopes.
- Use Apple Mail/IMAP account and mailbox allowlists.
- Separate personal and company mail into different workspaces or deployments.

## Data quality

- Benchmark HWP adapters on real HWP/HWPX samples before selecting a default.
- Keep previous extraction active until shadow output passes quality and contract tests.
- Maintain 30-50 pilot golden questions, then at least 100 before broad rollout.
- The current reviewed private retrieval set has 19 cases. It is below the
  30-50 question pilot target and does not certify answer/refusal quality.
- The checked-in portable suite has 100 positive and 20 ACL-negative cases. It
  is a deterministic merge gate, not a substitute for reviewed private
  questions or a frozen holdout.
- Verify XLSX search recall separately from numeric range-read correctness.
- Treat the six-document public scorecard as a harness validation, not a
  production corpus result. Add near-duplicates, revisions, tables, stale
  sources, and reviewed internal paraphrases before changing the activation
  decision.

## Operations

- Require the current commit to pass Python 3.12 and 3.13 CI, generated-contract
  and architecture checks, Ruff, mypy, runtime dependency audit, migrations,
  tests, and the 75% coverage floor.
- Verify both the release directory and archive. Record the wheel, archive,
  SPDX SBOM, SLSA provenance, image lock, manifest, and checksums.
- Publish only from a `v$(cat VERSION)` tag after quality and distribution jobs
  pass. Verify the GHCR digest and GitHub attestations against the repository.
- Never deploy a branch candidate or a `local/kip` image reference. Use one
  immutable image digest for migration, API, and worker.
- Test restore into a new empty database and absent or empty CAS path.
- Keep encrypted off-host backups under an explicit retention policy, and
  preserve the checksummed restore-drill receipt with measured RPO/RTO.
- Monitor failed jobs, extraction failure rate, stale-source warnings, queue age, and search latency.
- Pin production image digests and schedule upgrades.
- Enable Dependabot and `upstream-watch`, create the `dependencies` and
  `quality-candidate` labels, and route notifications to a named owner.
- Require changelog/license review, shadow evaluation, rollback evidence, and
  explicit activation for every parser, model, database, and runtime upgrade.
- Rebuild lexical, vector, and graph projections independently.
- Do not activate semantic retrieval or Neo4j without the gates documented in
  PRD/TRD. Installing pgvector/HNSW is readiness, not activation.
- Require `projection verify --name semantic` parity and pass the full,
  fingerprint-matched evaluation report to `projection activate --report ...
  --candidate ...`. The command rejects non-promoted, stale-code, and
  stale-configuration reports. Enabling semantic search in configuration
  remains a separate reviewed change.
- Keep the model sidecar on loopback with telemetry disabled and monitor its
  memory separately from Python RSS on Apple unified-memory systems.
