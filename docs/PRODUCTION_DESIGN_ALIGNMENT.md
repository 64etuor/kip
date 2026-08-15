# Production Design Alignment

- **Status:** Living conformance register
- **Last verified:** 2026-08-13
- **Target:** `docs/PRD.md`, `docs/TRD.md`, and the approved
  `docs/plans/2026-08-09-production-rag-starter-kit-design.md`
- **Current truth:** generated contracts, the current checkout, measured
  evaluation reports, and `docs/IMPLEMENTATION_STATUS.md`

This document prevents an approved target design from being mistaken for
implemented or production-accepted behavior. A target requirement remains a
gap until the current contract and runtime evidence satisfy it. Conversely, an
implementation change does not silently rewrite the target: it needs an ADR
and synchronized updates to the affected canonical documents.

## Document roles

| Document | Authority |
|---|---|
| `PRD.md` | Normative product requirements and release outcomes |
| `TRD.md` | Normative technical target and architecture invariants |
| `DATA_CONTRACTS.md` and generated schemas | Current public edge contracts |
| ADRs | Accepted design changes and their consequences |
| `IMPLEMENTATION_STATUS.md` | Current readiness and known limitations |
| This document | Target-to-current conformance and the ordered gap register |
| Dated plans and audits | Historical intent or evidence; never current status by themselves |

## Current alignment

| Area | Production intent | Current implementation and evidence | Verdict |
|---|---|---|---|
| Canonical evidence and ingestion | PostgreSQL owns source identity, immutable revisions, ACL state, active extraction, and approved assertions; parser/projection failures never replace valid state | Implemented for pilot paths, including CAS capture, shadow extraction, guarded activation, current-revision reads, and disposable lexical/vector projections | Aligned for pilot; connector-specific failure semantics still need target-environment validation |
| Application boundaries | Focused use cases depend on capability ports; concrete adapters are selected only at composition | `Application` composes ingestion, retrieval, evidence, answer, knowledge, operations, telemetry, ontology, and interaction use cases. Architecture checks prohibit application-to-adapter imports | Aligned |
| Edge architecture and filters | CLI, REST, and MCP expose the same use-case semantics; the SDK consumes stable REST/OpenAPI contracts | `SearchRequest` mode and filters have CLI/REST/MCP/SDK parity and every edge calls the same retrieval service. CLI envelopes retain typed errors although process exit codes are coarse; list/search edges remain limit-only rather than cursor-paginated | Aligned for search semantics; pagination and exit-code granularity remain |
| Agent-guided setup | One-question-at-a-time inspect/plan/apply/verify flow writes approved local artifacts atomically and emits a redacted receipt | The setup state machine, safe-root checks, `env:`/`file:`-only secret references (unresolvable schemes rejected at answer time), atomic apply, runtime-readiness verification, and receipt contracts with `next_steps` are implemented. `scripts/app-up.sh` layers the generated Compose override and `.mcp.json` selects the host-path config, so the approved plan is runnable without manual Compose or config edits. A 2026-08-10 ephemeral no-context acceptance passed | Aligned for starter acceptance; each recipient must rerun with its real mounts, identity, backup, and egress decisions |
| Runtime topology | Digest-pinned non-root API, worker, migration, and PostgreSQL services; sources read-only; identity/TLS/secrets supplied by the deployment | `compose.production.yaml`, role templates, resource limits on all services, read-only containers, private database network, and loopback API binding exist. The API bind-mounts `${KIP_NAS_PATH}` read-only (evidence freshness and `xlsx-read` need the live source tree) and `${KIP_ONTOLOGY_PATH}` read-write (discovery auto-release, ADR-044; read-only on the worker), its healthcheck targets the database round-trip `/readyz`, and the worker has a database-connectivity healthcheck. It is a reference Compose deployment, not an orchestrator, TLS edge, or secret manager | Partial: hardened reference ready, environment control plane remains external |
| Connector coverage | Filesystem/NAS, Slack, and mail preserve stable IDs, revisions, cursors, tombstones, ACL snapshots, and outage safety | Filesystem is pilot-validated, including complete-scan deletion grace reconciliation with soft tombstone revisions and reappearance re-indexing (migration 0020); failed and empty scans never contribute deletion evidence, though the TRD's sentinel/count-drop mount guards remain unimplemented. Slack, Apple Mail, and IMAP are reference adapters requiring provider-specific scope, edit/delete, rate-limit, retention, permission, and UID validation | Partial by design |
| Parser, OCR, and exact evidence | Parsers emit reproducible locators through shadow activation; OCR candidate evidence is locally available by default; spreadsheet facts use exact original ranges | PDF and PPTX share Kordoc 4.7.3 PP-OCRv5 Korean. New bootstrap/container installs verify the runtime and model cache before offline indexing. It preserves native units, adds page/shape/bbox OCR evidence, enforces image budgets, and fails back explicitly. SolarEdge image-deck QA produced 152 located OCR units; a scanned drawing recovered 2 units/864 characters; all sources were unchanged. XLSX returns exact bounded JSON-safe ranges and PPTX native structure remains typed | Partial: reviewed CER/WER, HWP locator, legacy Office/ODF/MSG/image parsers, PPTX media/OLE/legacy `.ppt`, and broad retrieval-quality gates remain |
| Identity, ACL, and model egress | Trusted identity creates `RequestContext`; ACL/freshness filter before every retrieval; model transfer is classification-aware and fail-closed | JWT/API-key adapters, dynamic ACL snapshots, RLS, canonical classification, and atomic egress decisions exist. Repository/RLS probes found zero leaks. Local loopback models are not remote egress | Aligned for pilot; recipient IdP/revocation and provider contract remain adoption gates |
| Active retrieval | Exact and lexical retrieval remain the baseline until a reviewed candidate passes all gates | Active path is the fixed `plan -> analyze -> abstain -> ranked pool -> diversify/truncate` pipeline with candidate-local BM25 and RapidFuzz fallback. The 19-case reviewed floor is Recall@10 `0.7895`, MRR `0.6462` | Aligned with lexical-first policy; quality is not broad-rollout level |
| Retrieval regression gate | Regressions are blocked before promotion or merge | Hosted CI and `verify.sh` always run a deterministic 100-positive/20-ACL portable gate. The separate private floor fails closed when protected runners set `KIP_REQUIRE_PRIVATE_GOLDEN=1`; a local missing-corpus skip is not private evidence | Aligned for portable merge protection; protected private runner remains a deployment gate |
| Pgvector production profile | The supported PostgreSQL reference profile carries a rebuildable vector projection and production ANN index while activation stays optional | Normal migrations install pgvector, the 1024d projection, and HNSW; vector queries use bounded strict iterative scan. EXPLAIN selected the HNSW index. `capabilities.semantic_search` requires a compatible complete verified active space | Aligned under ADR-037; extension-free deployment is not a supported profile |
| Review surface | Production design is headless; review is available through stable machine-readable edges | CLI/API/MCP review workflows exist with triage-ordered versioned candidate listings, catalog-derived evidence enforcement, audited assertion revocation, and supersede-on-approve (ADR-038, migration 0019); there is no bundled web review UI | Aligned for headless review; a review UI remains absent by design |
| Semantic projection | Versioned, ACL-filtered shadow spaces are evaluated before explicit activation | Qwen3 0.6B space is complete at `30,565/30,565`. HNSW vector Recall@10/MRR is `0.947/0.822`, P95 is `133.75 ms`, failed cases and ACL leaks are zero. Stale-warning coverage is absent. No activation ran | Aligned: quality and latency pass; freshness evidence fails closed |
| Semantic operations | Rebuilds are repeatable, bounded, and cannot mix incompatible inputs | Rebuild resumes missing/source-hash-stale rows, verifies against the same current-unit denominator, and versions the `head_tail_v1` 4,000-character input policy in the space identity. Rows store source hash but not the final normalized per-input hash required by `FR-VEC-002` | Partial after ADR-035; input-hash contract remains |
| Answer and refusal | Exact fresh evidence produces cited claims; insufficient or ambiguous evidence produces a typed refusal | Search, exact reopen, freshness, citation validation, and optional generation are shared. Identifier, numeric, focused-fact, and short multi-document adequacy gates return `answer_not_present` or `clarification_required`; targeted regressions pass | Aligned for bounded pilot; broad reviewed end-to-end calibration remains |
| Ontology RAG and graph | Only reviewed assertions enter current graph context; every path reopens exact, ACL-visible evidence; Neo4j is optional | Typed candidates, mining/review, migration lineage, approved PostgreSQL graph context, exact evidence, and evaluation contracts exist. The curation loop is executable end to end (ADR-038): approved-entity-aware re-mining, per-proposal skip reporting on durable job results, reversible review, and opt-in clearly-marked candidate visibility. Approving an entity-type or predicate discovery candidate now materializes an additive, shadow-validated ontology release automatically (ADR-044; long-running processes need a restart to load it, per-invocation CLI does not). Graph traversal runs inside the active repository backend (`capabilities.graph_backend` reports it; Neo4j is a future optional read projection with no shipped scaffolding), and broad private ontology quality has not been established | Ready for pilot, not broad ontology-RAG acceptance |
| Observability | Vendor-neutral redacted traces/metrics expose pipeline decisions without creating a second sensitive corpus | Closed PostgreSQL query traces, admin inspection/pruning, and optional OTLP spans/metrics are implemented, and `ops-report.sh` adds a schedulable single-command health summary (failed jobs, queue age, sync age, disk, backup age, `/readyz`) with an optional failure webhook. Collector delivery is non-blocking; dashboards, paging, and alert push beyond the webhook are deployment responsibilities | Aligned for pilot; push alerting and dashboards remain environment-specific |
| Backup and recovery | PostgreSQL, CAS, configuration, migrations, ACL/RLS, and rebuildable projections restore into an empty target with fingerprints | Sealed backup/restore tooling, `--retain` pruning, a redacted configuration snapshot with seal-and-verify rescan, a launchd daily schedule, and a checksummed restore drill are implemented; the 2026-08-10 acceptance restored canonical data and a real sealed set was checksum-verified on this host on 2026-08-13. Encryption, off-host retention, and push alerting beyond the ops-report webhook are external | Ready for operational adoption, not a managed backup service |
| Packaging and private handoff | Versioned wheel/images/Compose/contracts/skills/SBOM/provenance/checksums contain no private corpus or credentials | Clean-wheel and hardened-image smoke, deterministic release bundle, private-data scan, SBOM, provenance, image locks, and tag-only publish workflow exist. A local or branch candidate does not prove the remote tagged publish path ran | Candidate-ready; release publication remains per-tag evidence |
| ACL negative testing | Unauthorized principals and scopes reveal neither content nor existence | Repository/RLS probes reported zero leaks. Explicit local CLI scope options replace ambient grants, and an explicit empty set remains empty. A real-Postgres integration test (`test_postgres_cross_scope_acl_denies_search_read_and_graph`) now proves cross-scope denial with two distinct ACL scopes across search, direct unit read, and graph neighbors/path, so a regression in the RLS policies or the SQL ACL predicates fails the suite | Aligned at repository and local edge boundaries |
| Production acceptance | Blank-environment setup, 100+ reviewed questions, end-to-end answer/refusal evidence, restore, latency, and all edge checks pass | Distribution and portable contracts are implemented, but the reviewed private retrieval set has 19 cases, stale-warning evidence is absent, answer coverage is not broad, and environment-specific connectors/providers require validation | Not broad-production accepted |

## Current measured retrieval snapshot

The authoritative semantic decision is
`evaluation/reports/semantic-qwen3-all-modes-final-20260813/decision.md`.

| Variant | Recall@10 | MRR | P95 | ACL leaks |
|---|---:|---:|---:|---:|
| Active lexical + BM25 | 0.789 | 0.646 | 7385.80 ms | 0 |
| Vector HNSW shadow | **0.947** | **0.822** | **133.75 ms** | 0 |
| Hybrid shadow | 0.895 | 0.702 | 7924.98 ms | 0 |
| Reranked shadow | 0.842 | 0.656 | 15512.58 ms | 0 |

The vector channel solves a real candidate-recall problem: semantic-paraphrase
Recall@10 improves from `0.429` to `0.857`, while exact identifier and exact
numeric Recall@10 remain `1.000`. That is retrieval evidence only. It does not
establish final-answer correctness, citation quality, refusal appropriateness,
or an industry-leading result.

## Context-free manual QA snapshot

These 2026-08-13 probes used fresh agents without KIP command context. They are
manual observations, not an immutable golden report, and therefore cannot be
used as a promotion gate.

| Scenario | Observation | Disposition |
|---|---|---|
| Exact identifier | Expected evidence retrieved | Pass |
| Exact numeric | Expected evidence retrieved | Pass |
| Narrow XLSX exact read | Original range read succeeded | Pass |
| Supplier paraphrase | Relevant evidence was lexical rank 9; extractive answer selected unrelated content | Retrieval/answer gap |
| Korean-English/code-switched query | Relevant evidence was rank 10 and can fall outside a smaller default result window | Candidate-recall gap |
| Related words but no factual answer | Focused-fact adequacy regression cases now return `answer_not_present` | Bounded fix; expand reviewed cases |
| Unknown identifier | Search may show low-score discovery hits, while the answer edge now returns `answer_not_present` | Safe answer behavior; search display remains a quality issue |
| Ambiguous question | Short multi-document answer requests now return `clarification_required` | Bounded fix; expand reviewed cases |
| Outsider ACL | Explicit singular, plural, and empty scope inputs replace the ambient value and return no unauthorized result | Pass |
| Wide XLSX range with typed cells | Date/datetime/time/duration values, cached formula dates, array/data-table formulas, non-finite numerics, merges, filters, and blank padding remain strict JSON with exact coordinates | Pass at parser, storage-validation, and contract boundaries |
| SolarEdge PPTX corpus | 55/55 files parsed; 52 succeeded, two surfaced `SKIPPED_OLE`, one surfaced `SKIPPED_MEDIA`; 77,922 native units serialized and source stats were unchanged. Korean OCR added 152 located units across two screenshot-heavy decks with unchanged sources and explicit low-confidence warnings | Native parser and OCR integration pass; reviewed CER/WER, retrieval quality, media/OLE, and legacy `.ppt` coverage remain |

## Ordered next gates

1. Run the reviewed floor on a protected corpus-bearing pre-merge runner with
   `KIP_REQUIRE_PRIVATE_GOLDEN=1`.
2. Keep semantic search disabled until stale-warning coverage passes on a
   fingerprinted report and the space is explicitly activated; current quality,
   latency, failed-case, and ACL gates pass.
3. Expand to at least 30-50 reviewed private pilot questions, then 100+ plus a
   frozen holdout for broad rollout.
4. Expand end-to-end answer/citation/refusal and ambiguity cases beyond the
   bounded adequacy regressions, especially code-switched and answerless queries.
5. Store the normalized final per-row embedding-input hash required by
   `FR-VEC-002` and revalidate resumable rebuild semantics.
6. Complete HWP locator, OCR, restore, connector/provider, pagination,
   push-alerting, and recipient-environment acceptance evidence; ontology
   discovery promotion also still requires a manual reviewed YAML release.
