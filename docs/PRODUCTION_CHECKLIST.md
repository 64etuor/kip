# Production checklist

The default Compose profile is a local pilot profile. Before serving multiple users or indexing sensitive mail/Slack data:

## Identity and database

- Create separate migration, worker, read-agent, reviewer, and backup roles.
- Do not run API or worker with `kip_owner`.
- Put API behind an organization-approved identity-aware proxy.
- Derive workspace, principal, and ACL scopes from trusted identity; do not trust arbitrary client headers at an internet-facing boundary.
- Run RLS tests using non-owner roles and verify inaccessible graph paths return no evidence of existence.

## Network and secrets

- Keep PostgreSQL, Neo4j, and MCP stdio off public interfaces.
- Bind the reference API to loopback or a private network.
- Store secrets in the platform secret manager, not `.env` in production.
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
- Verify XLSX search recall separately from numeric range-read correctness.
- Treat the six-document public scorecard as a harness validation, not a
  production corpus result. Add near-duplicates, revisions, tables, stale
  sources, and reviewed internal paraphrases before changing the activation
  decision.

## Operations

- Test restore into a new database and CAS path.
- Monitor failed jobs, extraction failure rate, stale-source warnings, queue age, and search latency.
- Pin production image digests and schedule upgrades.
- Enable Dependabot and `upstream-watch`, create the `dependencies` and
  `quality-candidate` labels, and route notifications to a named owner.
- Require changelog/license review, shadow evaluation, rollback evidence, and
  explicit activation for every parser, model, database, and runtime upgrade.
- Rebuild lexical, vector, and graph projections independently.
- Do not activate pgvector or Neo4j without the gates documented in PRD/TRD.
- Require `projection verify --name semantic` parity and pass the full,
  fingerprint-matched evaluation report to `projection activate --report ...
  --candidate ...`. The command rejects non-promoted, stale-code, and
  stale-configuration reports. Enabling semantic search in configuration
  remains a separate reviewed change.
- Keep the model sidecar on loopback with telemetry disabled and monitor its
  memory separately from Python RSS on Apple unified-memory systems.
