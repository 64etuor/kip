# Changelog

## 3.2.0 - 2026-08-10

- Split application capabilities and repository ports so CLI, REST, MCP, and
  adapters compose through strict typed boundaries without vendor leakage.
- Added resumable agent-guided setup with one-question inspection, bounded
  source inventories, read-only mounts, secret references, deterministic plans,
  atomic apply/verify receipts, and generated MCP runtime configuration.
- Added trusted API-key/JWT identity, fresh ACL snapshots, classification-aware
  model egress, structured OpenAI/Anthropic generation, exact-evidence claim
  verification, and redacted RAG telemetry.
- Added typed ontology entity and relation candidates, durable relation-mining
  jobs, human review, approved graph context in answers, ontology migration
  materialization, and reviewed answer/ontology evaluation gates.
- Added a digest-pinned non-root container, hardened production Compose profile,
  hash-locked dependencies, deterministic wheel/starter bundle, SPDX SBOM,
  SLSA provenance, secret/private-data scans, and tag-only attested publishing.
- Added sealed PostgreSQL/CAS/config backup, explicit empty-target restore,
  manifest and RLS verification, projection rebuild, evaluation comparison, and
  checksummed recovery-drill receipts.

## 3.1.0 - 2026-07-28

- Added PostgreSQL 18 canonical store and pgvector-capable reference image.
- Added shared application layer for CLI, REST, and optional MCP.
- Added root `AGENTS.md`, root `CLAUDE.md`, project Skill, and root `.mcp.json`.
- Added filesystem, Slack, IMAP, Apple Mail, HWP broker, PDF, and XLSX adapter boundaries.
- Added shallow XLSX index and exact workbook range reader.
- Added ontology, assertion candidate, approved assertion, ACL, and graph query contracts.
- Added optional Neo4j projection adapter boundary without making it canonical.
- Added source-neutral `sync run`, stable top-level `xlsx-read`, projection management, canonical export, and assertion explanation commands.
- Added REST connector events, generic durable sync enqueueing, job polling, assertion explanation, OpenAPI contracts, MCP tools, and a Python application client.
