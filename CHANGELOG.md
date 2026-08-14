# Changelog

## Unreleased

- Added judge-proposed golden-dataset growth (ADR-045): an LLM judge
  authors `kip.golden-draft.v1` case proposals with per-case confidence
  and rationale; `kip evaluate draft validate/review/promote` runs the
  human sample-audit and fail-closed promotion (refused below the minimum
  sample rate, on any sampled rejection, or on ID collision) into a
  reviewed golden dataset, with judge provenance recorded per case and
  draft/review schemas published under `contracts/`. Canonical-authority
  fields (`lifecycle`/`version`/`reviewer`/`source_revision`) cannot be
  judge-proposed — a draft setting them is rejected — and are assigned at
  promotion from explicit options and the auditing reviewer, so promoted
  datasets satisfy `gate_eligible`. The judge generates; only
  human-authorized promotion creates canonical truth.
- Raised capacity defaults sized for older model context windows
  (`docs/plans/2026-08-14-llm-capability-scaling.md`): context pack
  40k -> 120k chars (item cap 8k -> 16k), answer evidence budget
  12k -> 32k chars (ceiling 40k -> 200k), generation 8 -> 16 claims and
  1024 -> 4096 output tokens, relation mining 50 -> 200 units /
  120k -> 480k chars / 32 -> 128 entity and 64 -> 256 relation proposals,
  ontology answer context 8 -> 16 entities and 50 -> 150 edges. All remain
  config keys with unchanged code-side hard bounds.

- Made ontology discovery approval materialize an additive release
  automatically (ADR-044): accepting an entity-type or predicate candidate
  writes the symbol into the ontology YAML tree with comment-preserving
  targeted edits, a minor version bump, review-policy sync, shadow
  validation, atomic apply, idempotent retries, and fail-closed behavior on
  read-only roots; discovery proposals gained optional spec fields
  (`parent`, `domain`, `range`, `inverse`, `risk`, `review`, `extraction`)
  on CLI/REST/MCP, persisted by migration 0021; auto-released predicates
  default to review-required/high-risk; review responses carry a
  `kip.ontology-release.v1` payload with `catalog_refresh:
  "restart_required"` for long-running processes; candidate-proposal
  surfaces now default `ontology_version` to the active catalog version.
- Made the ontology contract writable in containers for auto-release: the
  image no longer bakes `/app/ontology` immutable, and both Compose profiles
  bind-mount the version-controlled checkout (`KIP_ONTOLOGY_PATH`, default
  `./ontology` in development, required in production) read-write into the
  API and read-only into the worker, so approved releases persist across
  restarts and stay reviewable through git history.
- Required a `CHANGELOG.md` entry for every user-visible behavior, contract,
  configuration, or deployment change in the `AGENTS.md` change workflow.
- Enabled the conversational curation loop by default in the shipped
  configurations: `interaction.enabled = true` and
  `ontology.adaptive_discovery = true` in the example and container
  profiles; guided setup still records the explicit consent decision and
  `disabled` remains supported.
- Hardened the ontology contract validation (ADR-043): a domain profile that
  redefines a core entity type or core predicate name, and a
  `sources/*.yaml` object type whose `parent` is not a known entity type,
  now fail `kip ontology validate`, `OntologyRelease.load`, and container
  startup instead of silently shadowing core semantics.
- Added `POST /v1/review/candidates` (admin), the REST equivalent of CLI
  `review propose`: records a human-origin assertion candidate that enters
  the same review queue and is never auto-approved.
- Added the missing `limit` parameter to the MCP `kip_graph_neighbors` tool,
  matching the CLI and REST graph-neighbors contract.
- Added a real-PostgreSQL cross-scope ACL integration test proving that a
  principal holding one scope cannot see another scope's content through
  search, direct unit read, or graph neighbors/path.
- Removed configuration keys that were documented but never read
  (`database.pool_min`/`pool_max`, `storage.snapshot_*`,
  `security.max_zip_*`, `search.lexical_limit_default`, `graph.max_*`) from
  the example and container configuration, and set the real
  `database.pool_max_size` in the container profile, which previously fell
  back silently to the built-in default.
- Added structured PPTX extraction (ADR-041): pinned `python-pptx` plus a
  bounded read-only OOXML scan emit typed shape-level units for text, merged
  tables, chart caches, images (with SHA-256), speaker notes, legacy comments,
  and SmartArt, preserving slide/shape IDs, group paths, EMU geometry, source
  z-order, and a geometry-derived reading order; malformed optional parts keep
  primary content with a `partial` extraction and typed warnings; macros,
  external relationships, and embedded OLE objects are never executed or
  expanded, and ZIP entry/size/ratio limits apply before model construction.
- Added Korean OCR candidate enrichment (ADR-042): a typed optional `OcrPort`
  backed by a version-pinned Kordoc 4.7.3 executable (PP-OCRv5 Korean) appends
  `pdf_ocr` units with page and pixel bounding boxes for low-text, private-use,
  control-character, or replacement-character PDF pages and OCRs bounded PPTX
  images, while native `pdf_page` and shape units stay canonical.
- Made the ontology curation loop reviewable end to end (ADR-038): the mining
  job digest includes the approved-entity-set hash so the two-pass
  mine -> approve entities -> re-mine loop actually runs; invalid, duplicate,
  and stale-evidence proposals are skipped with per-proposal reasons recorded
  on a durable `kip.ontology-mining-result.v1` job payload (visible via
  `kip jobs list`, `GET /v1/jobs`, and the new MCP `kip_jobs` tool); evidence
  and review enforcement is derived from the ontology catalog and pinned to
  `predicates.yaml` by a contract test; candidate listings return the
  triage-ordered `kip.assertion-candidate-listing.v1` contract with display
  names, Korean predicate labels, and ACL-gated evidence snippets;
  `include_candidate_assertions` now surfaces clearly-marked proposed
  candidates on ontology-context-bearing surfaces; and Korean
  `label_ko`/`description`/`description_ko` metadata covers all predicates and
  entity types without an ontology version bump.
- Added assertion review governance: migration 0019 revocation audit columns,
  `kip review revoke`, `POST /v1/review/assertions/{id}/revoke`, MCP
  `kip_ontology_assertion_revoke`, exclusion of revoked assertions from every
  approved-only surface, and `--supersede-contradicted` on approval.
- Added filesystem deletion reconciliation (ADR-039): migration 0020 absence
  tracking, `[sync] deletion_grace_scans` (default 2), complete-scan-only
  fail-safe semantics (failed scans mark nothing; empty scans skip with a
  warning), soft tombstone revisions through the shared ingest path,
  reappearance re-indexing, and `absent`/`tombstoned` sync-summary fields.
- Fixed PostgreSQL re-sync of unchanged files failing with an ACL-snapshot
  ConflictError on every second run: configuration-owned snapshot timestamps
  refresh while snapshot identity fields stay strictly verified.
- Made guided setup end in a runnable deployment (ADR-040): fail-fast
  Python 3.12+ bootstrap, `env:`/`file:`-only secret references rejected at
  answer time, runtime-readiness verify checks, receipt `next_steps`, the
  host-path `config/kip.host.generated.toml` selected by `.mcp.json`, and
  `scripts/app-up.sh` layering the generated Compose override; the generated
  default search mode is `reranked` per ADR-034.
- Hardened the production topology: read-only `${KIP_NAS_PATH}` bind on the
  API service, a `/readyz` database round-trip endpoint and healthcheck
  (`/healthz` stays liveness-only), a worker database-connectivity
  healthcheck, and resource limits on all services.
- Added operations tooling: backup `--retain` retention pruning and
  `--dry-run`, redacted configuration snapshots with a seal-and-verify secret
  rescan, launchd daily backup and optional `ops-report.sh` scheduling with
  generated newsyslog rotation and a double-worker guard, and
  `scripts/ops-report.sh` (failed jobs, queue age, sync age, disk free,
  backup age, API health, `--json`, `KIP_OPS_WEBHOOK`).

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
